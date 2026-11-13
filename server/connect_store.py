"""Sessions, wallets, and the double-entry ledger.

Postgres row locks are used when DATABASE_URL is Postgres. SQLite, used by tests,
takes the writer lock on the balance update itself. Amounts are integer cents.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

import db
import connect_money as money

PBKDF2_ROUNDS = 50_000


class ConnectError(Exception):
    def __init__(self, code: str, status: int = 400, message: str = "", extra: dict | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = message or code
        self.extra = extra or {}


def now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None = None) -> str:
    return (moment or now()).isoformat()


def _id_col(dialect: str) -> str:
    return "id bigserial primary key" if dialect == "postgres" else "id integer primary key"


def ensure(con) -> None:
    ident = _id_col(con.dialect)
    statements = [
        """create table if not exists ac_astrologer(
            id text primary key,
            email text not null unique,
            password_hash text not null,
            name text not null,
            specialties text not null,
            languages text not null,
            years int not null,
            bio text not null,
            rate_cents int not null default 0,
            rating_tenths int not null default 0,
            status text not null,
            online int not null default 0,
            stripe_account text,
            created text not null)""",
        """create table if not exists ac_astro_token(
            token text primary key,
            astrologer_id text not null,
            created text not null)""",
        """create table if not exists ac_wallet(
            device text primary key,
            stripe_cents int not null default 0,
            apple_cents int not null default 0)""",
        f"""create table if not exists ac_ledger(
            {ident},
            tx_id text not null,
            account text not null,
            debit_cents int not null,
            credit_cents int not null,
            reason text not null,
            ref text,
            created text not null)""",
        """create table if not exists ac_payment(
            id text primary key,
            provider text not null,
            product_id text not null,
            device text not null,
            gross_cents int not null,
            created text not null)""",
        """create table if not exists ac_session(
            id text primary key,
            device text not null,
            astrologer_id text not null,
            mode text not null,
            rate_cents int not null,
            status text not null,
            hold_stripe int not null default 0,
            hold_apple int not null default 0,
            billed_cents int not null default 0,
            question text not null default '',
            chart_json text not null default '',
            idem_key text,
            room text,
            end_reason text,
            created_at text not null,
            started_at text,
            ended_at text,
            user_heartbeat text,
            astro_heartbeat text)""",
        f"""create table if not exists ac_message(
            {ident},
            session_id text not null,
            sender text not null,
            body text not null,
            created text not null)""",
        """create table if not exists ac_payout(
            id text primary key,
            astrologer_id text not null,
            amount_cents int not null,
            stripe_transfer text,
            status text not null,
            created text not null)""",
        """create table if not exists ac_earning(
            session_id text primary key,
            astrologer_id text not null,
            amount_cents int not null,
            platform_cents int not null,
            fee_cents int not null,
            available_at text not null,
            payout_id text)""",
        """create table if not exists ac_block(
            device text not null,
            astrologer_id text not null,
            created text not null,
            primary key(device, astrologer_id))""",
        """create table if not exists ac_report(
            id text primary key,
            device text not null,
            astrologer_id text not null,
            session_id text,
            reason text not null,
            created text not null)""",
        """create table if not exists ac_apple_question(
            transaction_id text primary key,
            device text not null,
            state text not null,
            ts text not null,
            consumed_at text)""",
    ]
    for stmt in statements:
        con.execute(stmt)
    con.execute("create unique index if not exists ac_session_idem on ac_session(device, idem_key)")
    con.execute("create index if not exists ac_message_session on ac_message(session_id, id)")
    con.execute("create index if not exists ac_ledger_tx on ac_ledger(tx_id)")


def _lock_wallet(con, device: str) -> tuple[int, int]:
    con.execute(
        "insert into ac_wallet(device, stripe_cents, apple_cents) values(?, 0, 0) on conflict(device) do nothing",
        (device,))
    sql = "select stripe_cents, apple_cents from ac_wallet where device=?"
    if con.dialect == "postgres":
        sql += " for update"
    row = con.execute(sql, (device,)).fetchone()
    return int(row[0]), int(row[1])


def _set_wallet(con, device: str, stripe_cents: int, apple_cents: int) -> None:
    if stripe_cents < 0 or apple_cents < 0:
        raise ConnectError("insufficient_balance", 402)
    con.execute(
        "update ac_wallet set stripe_cents=?, apple_cents=? where device=? and stripe_cents>=0 and apple_cents>=0",
        (int(stripe_cents), int(apple_cents), device))


def _post(con, tx_id: str, entries: list[tuple[str, int, int]], reason: str, ref: str) -> None:
    debit = sum(item[1] for item in entries)
    credit = sum(item[2] for item in entries)
    if debit != credit or debit < 0 or any(d < 0 or c < 0 for _, d, c in entries):
        raise ConnectError("unbalanced", 500)
    created = _iso()
    for account, d, c in entries:
        if d == 0 and c == 0:
            continue
        con.execute(
            "insert into ac_ledger(tx_id, account, debit_cents, credit_cents, reason, ref, created) values(?,?,?,?,?,?,?)",
            (tx_id, account, int(d), int(c), reason, ref, created))


def wallet_view(device: str) -> dict:
    with db.connect() as con:
        ensure(con)
        stripe_cents, apple_cents = _lock_wallet(con, device)
    total = stripe_cents + apple_cents
    return {
        "stripe_cents": stripe_cents,
        "apple_cents": apple_cents,
        "balance_cents": total,
        "balance_label": money.money_label(total),
    }


def credit_topup(device: str, provider: str, product_id: str, external_id: str) -> dict:
    if provider not in ("stripe", "apple"):
        raise ConnectError("bad_provider", 400)
    try:
        cents = money.product_cents(product_id)
    except money.MoneyError as e:
        raise ConnectError(e.code, 400) from e
    external_id = (external_id or "").strip()
    device = (device or "").strip()
    if not external_id or not device or len(device) > 200 or len(external_id) > 200:
        raise ConnectError("bad_payment", 400)
    with db.connect() as con:
        ensure(con)
        prior = con.execute("select gross_cents from ac_payment where id=?", (external_id,)).fetchone()
        if prior:
            view = wallet_view_in(con, device)
            view["credited"] = False
            return view
        stripe_cents, apple_cents = _lock_wallet(con, device)
        if provider == "stripe":
            stripe_cents += cents
            entries = [(f"cash:stripe", cents, 0), (f"wallet:{device}", 0, cents)]
        else:
            apple_cents += cents
            entries = [(f"cash:apple", cents, 0), (f"wallet:{device}", 0, cents)]
        _set_wallet(con, device, stripe_cents, apple_cents)
        con.execute(
            "insert into ac_payment(id, provider, product_id, device, gross_cents, created) values(?,?,?,?,?,?)",
            (external_id, provider, product_id, device, cents, _iso()))
        _post(con, "pay-" + external_id, entries, "wallet_topup", external_id)
        view = {"stripe_cents": stripe_cents, "apple_cents": apple_cents, "balance_cents": stripe_cents + apple_cents,
                "balance_label": money.money_label(stripe_cents + apple_cents), "credited": True}
    return view


def wallet_view_in(con, device: str) -> dict:
    stripe_cents, apple_cents = _lock_wallet(con, device)
    total = stripe_cents + apple_cents
    return {"stripe_cents": stripe_cents, "apple_cents": apple_cents, "balance_cents": total, "balance_label": money.money_label(total)}


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()
    return f"pbkdf2${salt}${digest}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if kind != "pbkdf2":
        return False
    candidate = hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(candidate, digest)


def _json_list(value) -> str:
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    else:
        parts = [str(p).strip() for p in (value or []) if str(p).strip()]
    return json.dumps(parts[:8])


def apply_astrologer(email: str, password: str, name: str, specialties, languages, years: int, bio: str) -> dict:
    email = (email or "").strip().lower()
    name = (name or "").strip()
    if "@" not in email or len(password or "") < 8 or len(name) < 2:
        raise ConnectError("bad_application", 400)
    years = int(years)
    if years < 0 or years > 80:
        raise ConnectError("bad_application", 400)
    with db.connect() as con:
        ensure(con)
        if con.execute("select id from ac_astrologer where email=?", (email,)).fetchone():
            raise ConnectError("email_taken", 409)
        astro_id = "astro_" + secrets.token_hex(8)
        con.execute(
            """insert into ac_astrologer(id, email, password_hash, name, specialties, languages, years, bio, rate_cents, rating_tenths, status, online, created)
               values(?,?,?,?,?,?,?,?,0,0,'pending',0,?)""",
            (astro_id, email, hash_password(password), name[:80], _json_list(specialties), _json_list(languages), years, (bio or "")[:500], _iso()))
    return {"id": astro_id, "status": "pending"}


def astro_login(email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    with db.connect() as con:
        ensure(con)
        row = con.execute("select id, password_hash, name, status from ac_astrologer where email=?", (email,)).fetchone()
        if not row or not check_password(password or "", row[1]):
            raise ConnectError("unauthorized", 401)
        token = secrets.token_urlsafe(32)
        con.execute("insert into ac_astro_token(token, astrologer_id, created) values(?,?,?)", (token, row[0], _iso()))
    return {"token": token, "astrologer_id": row[0], "name": row[2], "status": row[3]}


def astro_from_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        raise ConnectError("unauthorized", 401)
    with db.connect() as con:
        ensure(con)
        row = con.execute("select astrologer_id from ac_astro_token where token=?", (token,)).fetchone()
    if not row:
        raise ConnectError("unauthorized", 401)
    return row[0]


def set_online(astrologer_id: str, online: bool) -> dict:
    with db.connect() as con:
        ensure(con)
        row = con.execute("select status from ac_astrologer where id=?", (astrologer_id,)).fetchone()
        if not row:
            raise ConnectError("not_found", 404)
        if row[0] != "approved":
            raise ConnectError("not_approved", 403)
        con.execute("update ac_astrologer set online=? where id=?", (1 if online else 0, astrologer_id))
    return {"online": bool(online)}


def _astro_public(row) -> dict:
    (astro_id, name, specialties, languages, years, rate_cents, rating, online, status) = row
    specs = json.loads(specialties or "[]")
    langs = json.loads(languages or "[]")
    return {
        "id": astro_id,
        "name": name,
        "initial": (name[:1] or "?").upper(),
        "specialties": specs,
        "languages": langs,
        "years": years,
        "rate_cents": rate_cents,
        "rate_label": money.money_label(rate_cents, per_minute=True),
        "rating_tenths": rating,
        "rating_label": "New" if not rating else f"{rating // 10}.{rating % 10}",
        "online": bool(online),
        "status": status,
    }


def list_astrologers(device: str) -> list[dict]:
    with db.connect() as con:
        ensure(con)
        rows = con.execute(
            """select id, name, specialties, languages, years, rate_cents, rating_tenths, online, status
               from ac_astrologer where status='approved' and online=1 and rate_cents>0
               and id not in (select astrologer_id from ac_block where device=?)
               order by name""",
            (device,)).fetchall()
    return [_astro_public(row) for row in rows]


def admin_list() -> list[dict]:
    with db.connect() as con:
        ensure(con)
        rows = con.execute(
            """select id, name, specialties, languages, years, rate_cents, rating_tenths, online, status, email
               from ac_astrologer order by created""").fetchall()
    out = []
    for row in rows:
        item = _astro_public(row[:9])
        item["email"] = row[9]
        out.append(item)
    return out


def admin_decide(astrologer_id: str, approve: bool, rate_cents: int = 0, rating_tenths: int = 0) -> dict:
    with db.connect() as con:
        ensure(con)
        row = con.execute("select id from ac_astrologer where id=?", (astrologer_id,)).fetchone()
        if not row:
            raise ConnectError("not_found", 404)
        if not approve:
            con.execute("update ac_astrologer set status='rejected', online=0 where id=?", (astrologer_id,))
            return {"id": astrologer_id, "status": "rejected"}
        rate = money.hold_cents(int(rate_cents)) // money.MIN_MINUTES
        rating = max(0, min(50, int(rating_tenths or 0)))
        con.execute(
            "update ac_astrologer set status='approved', rate_cents=?, rating_tenths=? where id=?",
            (rate, rating, astrologer_id))
    return {"id": astrologer_id, "status": "approved", "rate_cents": rate, "rate_label": money.money_label(rate, True)}


def _load_astro(con, astrologer_id: str):
    return con.execute(
        "select id, name, status, online, rate_cents from ac_astrologer where id=?",
        (astrologer_id,)).fetchone()


def quote(device: str, astrologer_id: str) -> dict:
    with db.connect() as con:
        ensure(con)
        astro = _load_astro(con, astrologer_id)
        if not astro or astro[2] != "approved" or not astro[3]:
            raise ConnectError("unavailable", 404)
        blocked = con.execute(
            "select 1 from ac_block where device=? and astrologer_id=?", (device, astrologer_id)).fetchone()
        if blocked:
            raise ConnectError("blocked", 403)
        rate = int(astro[4])
        hold = money.hold_cents(rate)
        balance = wallet_view_in(con, device)
    return {
        "astrologer_id": astro[0],
        "name": astro[1],
        "rate_cents": rate,
        "rate_label": money.money_label(rate, True),
        "min_minutes": money.MIN_MINUTES,
        "hold_cents": hold,
        "hold_label": money.money_label(hold),
        **balance,
    }


def _session_row(con, session_id: str):
    return con.execute("select * from ac_session where id=?", (session_id,)).fetchone()


def _cols(con) -> list[str]:
    if con.dialect == "postgres":
        rows = con.execute(
            """select column_name from information_schema.columns
               where table_name='ac_session' order by ordinal_position""").fetchall()
        return [r[0] for r in rows]
    return [r[1] for r in con.execute("pragma table_info(ac_session)").fetchall()]


def _as_dict(con, row) -> dict:
    return dict(zip(_cols(con), row))


def start_session(device: str, astrologer_id: str, mode: str, question: str, chart: dict | None, idem_key: str | None) -> dict:
    if mode not in ("chat", "call"):
        raise ConnectError("bad_mode", 400)
    device = (device or "").strip()
    question = (question or "").strip()[:500]
    if not device or len(device) > 200:
        raise ConnectError("bad_device", 400)
    chart_json = json.dumps(chart or {}, ensure_ascii=False)[:8000]
    idem_key = (idem_key or "").strip()[:80] or None
    with db.connect() as con:
        ensure(con)
        if idem_key:
            prior = con.execute("select id from ac_session where device=? and idem_key=?", (device, idem_key)).fetchone()
            if prior:
                return session_view(prior[0], con)
        astro = _load_astro(con, astrologer_id)
        if not astro or astro[2] != "approved" or not astro[3] or int(astro[4]) <= 0:
            raise ConnectError("unavailable", 404)
        if con.execute("select 1 from ac_block where device=? and astrologer_id=?", (device, astrologer_id)).fetchone():
            raise ConnectError("blocked", 403)
        rate = int(astro[4])
        hold = money.hold_cents(rate)
        stripe_cents, apple_cents = _lock_wallet(con, device)
        try:
            take_stripe, take_apple = money.draw_buckets(stripe_cents, apple_cents, hold)
        except money.MoneyError as e:
            raise ConnectError(e.code, 402, extra={"hold_cents": hold, "balance_cents": stripe_cents + apple_cents}) from e
        session_id = "ses_" + secrets.token_hex(8)
        _set_wallet(con, device, stripe_cents - take_stripe, apple_cents - take_apple)
        _post(con, "hold-" + session_id, [
            (f"wallet:{device}", hold, 0),
            (f"hold:{session_id}", 0, hold),
        ], "session_hold", session_id)
        created = _iso()
        con.execute(
            """insert into ac_session(id, device, astrologer_id, mode, rate_cents, status, hold_stripe, hold_apple,
               billed_cents, question, chart_json, idem_key, room, created_at)
               values(?,?,?,?,?,'ringing',?,?,0,?,?,?,?,?)""",
            (session_id, device, astrologer_id, mode, rate, take_stripe, take_apple, question, chart_json, idem_key,
             "drishti_" + session_id, created))
        return session_view(session_id, con)


def session_view(session_id: str, con=None) -> dict:
    own = con is None
    if own:
        con = db.connect()
    try:
        if own:
            ensure(con)
        row = _session_row(con, session_id)
        if not row:
            raise ConnectError("not_found", 404)
        item = _as_dict(con, row)
        moment = now()
        started = _parse(item.get("started_at"))
        elapsed = int((moment - started).total_seconds()) if started and item["status"] == "active" else 0
        if item["status"] == "ended":
            elapsed = 0
            if item.get("started_at") and item.get("ended_at"):
                elapsed = max(0, int((_parse(item["ended_at"]) - _parse(item["started_at"])).total_seconds()))
        preview = item["billed_cents"] if item["status"] == "ended" else money.elapsed_cost(item["rate_cents"], elapsed)
        astro = con.execute("select name from ac_astrologer where id=?", (item["astrologer_id"],)).fetchone()
        balance = wallet_view_in(con, item["device"])
        return {
            "id": item["id"],
            "device": item["device"],
            "astrologer_id": item["astrologer_id"],
            "astrologer_name": astro[0] if astro else "",
            "mode": item["mode"],
            "status": item["status"],
            "rate_cents": item["rate_cents"],
            "rate_label": money.money_label(item["rate_cents"], True),
            "hold_cents": item["hold_stripe"] + item["hold_apple"],
            "elapsed_seconds": elapsed,
            "cost_cents": preview,
            "cost_label": money.money_label(preview),
            "end_reason": item.get("end_reason") or "",
            "question": item.get("question") or "",
            "chart": _chart(item.get("chart_json")),
            "server_now": _iso(moment),
            **{k: balance[k] for k in ("balance_cents", "balance_label")},
        }
    finally:
        if own:
            con.raw.commit()
            con.raw.close()


def _chart(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _require_party(con, item: dict, device: str | None, astrologer_id: str | None) -> str:
    if device and device == item["device"]:
        return "user"
    if astrologer_id and astrologer_id == item["astrologer_id"]:
        return "astrologer"
    raise ConnectError("unauthorized", 401)


def accept_session(session_id: str, astrologer_id: str) -> dict:
    with db.connect() as con:
        ensure(con)
        item = _as_dict(con, _session_row(con, session_id) or (_ for _ in ()).throw(ConnectError("not_found", 404)))
        if item["astrologer_id"] != astrologer_id:
            raise ConnectError("unauthorized", 401)
        if item["status"] == "active":
            return session_view(session_id, con)
        if item["status"] != "ringing":
            raise ConnectError("not_ringing", 409)
        started = _iso()
        con.execute(
            "update ac_session set status='active', started_at=?, user_heartbeat=?, astro_heartbeat=? where id=? and status='ringing'",
            (started, started, started, session_id))
        return session_view(session_id, con)


def heartbeat(session_id: str, role: str, device: str | None, astrologer_id: str | None) -> dict:
    with db.connect() as con:
        ensure(con)
        row = _session_row(con, session_id)
        if not row:
            raise ConnectError("not_found", 404)
        item = _as_dict(con, row)
        who = _require_party(con, item, device, astrologer_id)
        if role not in ("user", "astrologer") or role != who:
            raise ConnectError("unauthorized", 401)
        moment = now()
        column = "user_heartbeat" if who == "user" else "astro_heartbeat"
        con.execute(f"update ac_session set {column}=? where id=?", (_iso(moment), session_id))
        item = _as_dict(con, _session_row(con, session_id))
        _tick(con, item, moment)
        return session_view(session_id, con)


def _tick(con, item: dict, moment: datetime) -> None:
    if item["status"] == "ringing":
        created = _parse(item["created_at"])
        if created and (moment - created).total_seconds() >= 90:
            _settle(con, item, moment, "unanswered")
        return
    if item["status"] != "active":
        return
    started = _parse(item["started_at"])
    elapsed = int((moment - started).total_seconds()) if started else 0
    rate = int(item["rate_cents"])
    hold = int(item["hold_stripe"]) + int(item["hold_apple"])
    covered = money.paid_seconds(hold, rate)
    if elapsed + 25 >= covered:
        _extend_hold(con, item, rate)
        item = _as_dict(con, _session_row(con, item["id"]))
        hold = int(item["hold_stripe"]) + int(item["hold_apple"])
        covered = money.paid_seconds(hold, rate)
    if elapsed >= covered:
        _settle(con, item, moment, "balance")
        return
    for column, reason in (("user_heartbeat", "user_left"), ("astro_heartbeat", "astro_left")):
        beat = _parse(item.get(column))
        if beat and (moment - beat).total_seconds() > 50:
            _settle(con, item, moment, reason)
            return


def _extend_hold(con, item: dict, rate: int) -> None:
    stripe_cents, apple_cents = _lock_wallet(con, item["device"])
    try:
        take_stripe, take_apple = money.draw_buckets(stripe_cents, apple_cents, rate)
    except money.MoneyError:
        return
    _set_wallet(con, item["device"], stripe_cents - take_stripe, apple_cents - take_apple)
    con.execute(
        "update ac_session set hold_stripe=hold_stripe+?, hold_apple=hold_apple+? where id=? and status='active'",
        (take_stripe, take_apple, item["id"]))
    added = take_stripe + take_apple
    _post(con, "extend-" + item["id"] + "-" + str(int(item["hold_stripe"]) + int(item["hold_apple"]) + added), [
        (f"wallet:{item['device']}", added, 0),
        (f"hold:{item['id']}", 0, added),
    ], "session_extend", item["id"])


def end_session(session_id: str, device: str | None, astrologer_id: str | None, reason: str = "ended") -> dict:
    with db.connect() as con:
        ensure(con)
        row = _session_row(con, session_id)
        if not row:
            raise ConnectError("not_found", 404)
        item = _as_dict(con, row)
        _require_party(con, item, device, astrologer_id)
        if item["status"] == "ended":
            return session_view(session_id, con)
        _settle(con, item, now(), reason if item["status"] == "active" else "cancelled")
        return session_view(session_id, con)


def end_room(room: str, reason: str = "provider") -> dict | None:
    with db.connect() as con:
        ensure(con)
        row = con.execute("select id from ac_session where room=? and status in ('active','ringing')", (room,)).fetchone()
        if not row:
            return None
        item = _as_dict(con, _session_row(con, row[0]))
        if item["status"] == "ended":
            return session_view(item["id"], con)
        _settle(con, item, now(), reason if item["status"] == "active" else "cancelled")
        return session_view(item["id"], con)


def _settle(con, item: dict, moment: datetime, reason: str) -> None:
    if con.dialect == "postgres":
        con.execute("select id from ac_session where id=? for update", (item["id"],))
    current = _as_dict(con, _session_row(con, item["id"]))
    if current["status"] == "ended":
        return
    started = _parse(current.get("started_at"))
    elapsed = int((moment - started).total_seconds()) if started and current["status"] == "active" else 0
    rate = int(current["rate_cents"])
    hold_stripe = int(current["hold_stripe"])
    hold_apple = int(current["hold_apple"])
    hold = hold_stripe + hold_apple
    raw = money.elapsed_cost(rate, elapsed) if current["status"] == "active" else 0
    charge = min(hold, raw)
    # The charged cents keep the same Stripe-then-Apple mix as the hold.
    if hold:
        charge_stripe = min(hold_stripe, charge)
        charge_apple = charge - charge_stripe
    else:
        charge_stripe = charge_apple = 0
    refund_stripe = hold_stripe - charge_stripe
    refund_apple = hold_apple - charge_apple
    stripe_cents, apple_cents = _lock_wallet(con, current["device"])
    _set_wallet(con, current["device"], stripe_cents + refund_stripe, apple_cents + refund_apple)
    net, fee = money.net_of_apple(charge_stripe, charge_apple)
    astro_cents, platform_cents = money.split_net(net)
    entries = [(f"hold:{current['id']}", hold, 0)]
    if refund_stripe + refund_apple:
        entries.append((f"wallet:{current['device']}", 0, refund_stripe + refund_apple))
    if astro_cents:
        entries.append((f"payable:{current['astrologer_id']}", 0, astro_cents))
    if platform_cents:
        entries.append((f"revenue:platform", 0, platform_cents))
    if fee:
        entries.append(("fee:apple", 0, fee))
    if hold:
        _post(con, "settle-" + current["id"], entries, "session_settle", current["id"])
    available = _iso(moment + timedelta(days=7))
    con.execute(
        """insert into ac_earning(session_id, astrologer_id, amount_cents, platform_cents, fee_cents, available_at)
           values(?,?,?,?,?,?) on conflict(session_id) do nothing""",
        (current["id"], current["astrologer_id"], astro_cents, platform_cents, fee, available))
    updated = con.execute(
        """update ac_session set status='ended', ended_at=?, billed_cents=?, end_reason=?
           where id=? and status!='ended'""",
        (_iso(moment), charge, reason, current["id"]))
    if getattr(updated, "rowcount", 1) == 0:
        return


def post_message(session_id: str, sender: str, body: str, device: str | None, astrologer_id: str | None) -> dict:
    body = (body or "").strip()
    if not body or len(body) > 2000:
        raise ConnectError("bad_message", 400)
    with db.connect() as con:
        ensure(con)
        item = _as_dict(con, _session_row(con, session_id) or (_ for _ in ()).throw(ConnectError("not_found", 404)))
        who = _require_party(con, item, device, astrologer_id)
        if who != sender or item["status"] != "active":
            raise ConnectError("not_active", 409)
        con.execute(
            "insert into ac_message(session_id, sender, body, created) values(?,?,?,?)",
            (session_id, sender, body, _iso()))
        column = "user_heartbeat" if sender == "user" else "astro_heartbeat"
        con.execute(f"update ac_session set {column}=? where id=?", (_iso(), session_id))
    return {"ok": True}


def list_messages(session_id: str, after: int, device: str | None, astrologer_id: str | None) -> list[dict]:
    with db.connect() as con:
        ensure(con)
        item = _as_dict(con, _session_row(con, session_id) or (_ for _ in ()).throw(ConnectError("not_found", 404)))
        _require_party(con, item, device, astrologer_id)
        rows = con.execute(
            "select id, sender, body, created from ac_message where session_id=? and id>? order by id",
            (session_id, int(after))).fetchall()
    return [{"id": r[0], "sender": r[1], "body": r[2], "created": r[3]} for r in rows]


def issue_call(session_id: str, device: str | None, astrologer_id: str | None, api_key: str, api_secret: str, url: str) -> dict:
    with db.connect() as con:
        ensure(con)
        item = _as_dict(con, _session_row(con, session_id) or (_ for _ in ()).throw(ConnectError("not_found", 404)))
        who = _require_party(con, item, device, astrologer_id)
        hold = int(item["hold_stripe"]) + int(item["hold_apple"])
        if item["mode"] != "call" or item["status"] != "active" or not item.get("started_at") or hold <= 0:
            raise ConnectError("call_not_ready", 403)
    if not api_key or not api_secret or not url:
        raise ConnectError("call_unavailable", 503, "Voice is not configured on the server.")
    import connect_livekit
    identity = "user-" + item["device"] if who == "user" else "astro-" + item["astrologer_id"]
    token = connect_livekit.join_token(api_key, api_secret, item["room"], identity, ttl=600)
    return {"url": url, "room": item["room"], "token": token, "identity": identity, "ttl_seconds": 600}


def report(device: str, astrologer_id: str, session_id: str | None, reason: str) -> dict:
    reason = (reason or "").strip()[:300]
    if len(reason) < 3:
        raise ConnectError("bad_report", 400)
    with db.connect() as con:
        ensure(con)
        report_id = "rep_" + secrets.token_hex(6)
        con.execute(
            "insert into ac_report(id, device, astrologer_id, session_id, reason, created) values(?,?,?,?,?,?)",
            (report_id, device, astrologer_id, session_id, reason, _iso()))
    return {"id": report_id}


def block(device: str, astrologer_id: str) -> dict:
    with db.connect() as con:
        ensure(con)
        con.execute(
            "insert into ac_block(device, astrologer_id, created) values(?,?,?) on conflict(device, astrologer_id) do nothing",
            (device, astrologer_id, _iso()))
    return {"blocked": True}


def set_stripe_account(astrologer_id: str, account_id: str) -> None:
    with db.connect() as con:
        ensure(con)
        con.execute("update ac_astrologer set stripe_account=? where id=?", (account_id, astrologer_id))


def astrologer_private(astrologer_id: str) -> dict:
    with db.connect() as con:
        ensure(con)
        row = con.execute(
            "select email, stripe_account, status, online, name from ac_astrologer where id=?",
            (astrologer_id,)).fetchone()
    if not row:
        raise ConnectError("not_found", 404)
    return {"email": row[0], "stripe_account": row[1] or "", "status": row[2], "online": bool(row[3]), "name": row[4]}


def earnings(astrologer_id: str) -> dict:
    with db.connect() as con:
        ensure(con)
        rows = con.execute(
            """select s.id, s.mode, s.billed_cents, s.ended_at, e.amount_cents, e.platform_cents, e.fee_cents, e.available_at, e.payout_id
               from ac_session s left join ac_earning e on e.session_id=s.id
               where s.astrologer_id=? and s.status='ended' order by s.ended_at""",
            (astrologer_id,)).fetchall()
        payouts = con.execute(
            "select id, amount_cents, status, created, stripe_transfer from ac_payout where astrologer_id=? order by created",
            (astrologer_id,)).fetchall()
    sessions = [{
        "id": r[0], "mode": r[1], "billed_cents": r[2] or 0, "ended_at": r[3],
        "astro_cents": r[4] or 0, "platform_cents": r[5] or 0, "fee_cents": r[6] or 0,
        "available_at": r[7], "paid": bool(r[8]),
    } for r in rows]
    return {
        "sessions": sessions,
        "astro_cents": sum(s["astro_cents"] for s in sessions),
        "platform_cents": sum(s["platform_cents"] for s in sessions),
        "payouts": [{"id": p[0], "amount_cents": p[1], "status": p[2], "created": p[3], "stripe_transfer": p[4]} for p in payouts],
    }


def payout_candidates(moment: datetime | None = None) -> list[dict]:
    moment = moment or now()
    with db.connect() as con:
        ensure(con)
        rows = con.execute(
            """select e.astrologer_id, a.stripe_account, coalesce(sum(e.amount_cents), 0)
               from ac_earning e join ac_astrologer a on a.id=e.astrologer_id
               where e.payout_id is null and e.available_at<=? and e.amount_cents>0 and a.stripe_account is not null and a.stripe_account!=''
               group by e.astrologer_id, a.stripe_account""",
            (_iso(moment),)).fetchall()
    return [{"astrologer_id": r[0], "stripe_account": r[1], "amount_cents": int(r[2])} for r in rows if int(r[2]) >= 100]


def mark_payout(astrologer_id: str, amount_cents: int, transfer_id: str, moment: datetime | None = None) -> dict:
    moment = moment or now()
    payout_id = "pay_" + astrologer_id + "_" + moment.strftime("%G-W%V")
    with db.connect() as con:
        ensure(con)
        prior = con.execute("select status, stripe_transfer, amount_cents from ac_payout where id=?", (payout_id,)).fetchone()
        if prior and prior[1]:
            return {"id": payout_id, "status": prior[0], "amount_cents": prior[2], "created": False}
        if not prior:
            con.execute(
                "insert into ac_payout(id, astrologer_id, amount_cents, stripe_transfer, status, created) values(?,?,?,?, 'paid', ?)",
                (payout_id, astrologer_id, amount_cents, transfer_id, _iso(moment)))
        else:
            con.execute(
                "update ac_payout set stripe_transfer=?, status='paid', amount_cents=? where id=?",
                (transfer_id, amount_cents, payout_id))
        con.execute(
            "update ac_earning set payout_id=? where astrologer_id=? and payout_id is null and available_at<=?",
            (payout_id, astrologer_id, _iso(moment)))
        _post(con, "payout-" + payout_id, [
            (f"payable:{astrologer_id}", amount_cents, 0),
            ("cash:stripe", 0, amount_cents),
        ], "payout", payout_id)
    return {"id": payout_id, "status": "paid", "amount_cents": amount_cents, "created": prior is None}


def incoming(astrologer_id: str) -> list[dict]:
    with db.connect() as con:
        ensure(con)
        rows = con.execute(
            "select id from ac_session where astrologer_id=? and status in ('ringing','active') order by created_at",
            (astrologer_id,)).fetchall()
        return [session_view(r[0], con) for r in rows]


def redeem_apple_question(device: str, transaction_id: str, signed_transaction: str | None):
    import connect_storekit
    info = connect_storekit.verify_transaction(transaction_id, signed_transaction)
    if info.get("product_id") != money.ACHARYA_PRODUCT:
        raise ConnectError("payment_invalid", 400, "The App Store did not accept that purchase.")
    txn = info["transaction_id"]
    with db.connect() as con:
        ensure(con)
        row = con.execute("select device, state from ac_apple_question where transaction_id=?", (txn,)).fetchone()
        if row and row[1] == "consumed":
            raise ConnectError("payment_used", 400, "That purchase was already used.")
        if row and row[1] == "reserved" and row[0] != device:
            raise ConnectError("payment_in_progress", 409, "That purchase is already in use.")
        if row:
            con.execute("update ac_apple_question set state='reserved', device=?, ts=? where transaction_id=?", (device, _iso(), txn))
        else:
            con.execute(
                "insert into ac_apple_question(transaction_id, device, state, ts) values(?,?,'reserved',?)",
                (txn, device, _iso()))
    return {"kind": "apple", "id": txn}


def finish_apple(redemption: dict) -> None:
    with db.connect() as con:
        ensure(con)
        con.execute(
            "update ac_apple_question set state='consumed', consumed_at=? where transaction_id=? and state!='consumed'",
            (_iso(), redemption["id"]))


def release_apple(redemption: dict) -> None:
    with db.connect() as con:
        ensure(con)
        con.execute(
            "update ac_apple_question set state='released' where transaction_id=? and state='reserved'",
            (redemption["id"],))
