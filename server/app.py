"""Drishti backend: chart engine API, city search, AI chat (SSE), matching, horoscope, Play Billing."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import smtplib
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import engine
import gita
import llm
import playbilling
from matching import milan
from playbilling import PlayError

APP_NAME = "Drishti"
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(ROOT), "web")
QUESTION_PRICE = "0.99"
QUESTION_CURRENCY = "USD"
QUESTION_PRICE_LABEL = "$0.99"
# A crashed request must not lock a purchase forever, and a double-tap must not
# spend one purchase on two answers.
RESERVE_SECONDS = 120
_UNSET = object()
_play_override = _UNSET


def set_play_client(client):
    """Tests inject a fake Play client. None means Play is not configured."""
    global _play_override
    _play_override = client


def clear_play_client():
    """Return to reading Play credentials from the environment."""
    global _play_override
    _play_override = _UNSET


def play_backend():
    if _play_override is not _UNSET:
        return _play_override
    return playbilling.client_from_env()


def billing_public() -> dict:
    configured = False
    try:
        configured = play_backend() is not None
    except PlayError:
        configured = False
    return {
        "billing": "google_play",
        "package_name": playbilling.package_name(),
        "product_id": playbilling.product_id(),
        "price": QUESTION_PRICE,
        "currency": QUESTION_CURRENCY,
        "price_label": QUESTION_PRICE_LABEL,
        "free_questions": 0,
        "play_configured": configured,
    }

app = FastAPI(title=APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ------------------------------------------------------------------ db ----
_lock = threading.Lock()


def _ensure_wallet(con, device: str):
    con.execute("insert into wallet(device) values(?) on conflict(device) do nothing", (device,))


def wallet(device: str) -> dict:
    with _lock, db.connect() as con:
        _ensure_wallet(con, device)
        email = con.execute("select email from wallet where device=?", (device,)).fetchone()[0]
    # credits, free_left and packs stay at zero so an old copy of the app cannot
    # treat a balance as permission to ask. The server ignores them.
    return billing_public() | {
        "email": email, "credits": 0, "free_left": 0, "free_total": 0,
        "question_cost": 0, "packs": {},
    }


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_token_shape(token: str) -> str:
    token = (token or "").strip()
    if not token or len(token) < 16 or len(token) > 4096 or any(ch.isspace() for ch in token):
        raise PlayError("payment_invalid", "Google Play did not accept this purchase. The question was not answered.")
    return token


def _consume_now(token_hash: str, token: str | None):
    if not token:
        return
    backend = play_backend()
    if backend is None:
        return
    try:
        backend.consume(token)
    except Exception:
        return
    with _lock, db.connect() as con:
        con.execute(
            "update play_purchases set consumed_at=?, token=null where token_hash=?",
            (datetime.now(timezone.utc).isoformat(), token_hash))


def redeem_purchase(device: str, token: str) -> str:
    """Verify a Play purchase and reserve it for one in-flight question.

    Returns the token hash. Raises PlayError without calling the AI when the
    purchase is missing, unpaid, already used, or Play cannot be reached.
    The purchase is not consumed here. ``finish_purchase`` consumes it only
    after an answer has started, so a failed answer can be retried for free.
    """
    device = (device or "").strip()
    token = _check_token_shape(token)
    if not device or len(device) > 200:
        raise PlayError("payment_invalid", "Google Play did not accept this purchase. The question was not answered.")
    try:
        backend = play_backend()
    except PlayError:
        raise
    if backend is None:
        raise PlayError(
            "billing_not_configured",
            "Play Billing is not set up on the server yet. The question was not answered, and you were not charged again.")
    try:
        payload = backend.fetch(token)
    except PlayError:
        raise
    except Exception as e:
        raise PlayError(
            "billing_unavailable",
            "Google Play could not be reached. The question was not answered. Try again with the same purchase.") from e
    info = playbilling.validate_purchase(payload, playbilling.product_id())
    account = info["obfuscated_account"]
    if account and account != device:
        raise PlayError(
            "payment_invalid",
            "This purchase belongs to a different device. The question was not answered.")
    token_hash = _token_hash(token)
    now = datetime.now(timezone.utc)
    now_s = now.isoformat()
    stale_before = (now - timedelta(seconds=RESERVE_SECONDS)).isoformat()
    reject: PlayError | None = None
    with _lock, db.connect() as con:
        _ensure_wallet(con, device)
        row = con.execute(
            "select device, state, ts, consumed_at from play_purchases where token_hash=?",
            (token_hash,)).fetchone()
        if row is None:
            taken = con.execute(
                "select token_hash from play_purchases where order_id=?", (info["order_id"],)).fetchone()
            if taken:
                reject = PlayError(
                    "payment_used",
                    "That purchase was already used for a question. Each new question is $0.99.")
            else:
                state = "spent" if info["consumption_state"] == 1 else "reserved"
                # A unique clash must not abort the Postgres transaction.
                con.execute("savepoint play_insert")
                try:
                    con.execute(
                        "insert into play_purchases(token_hash, order_id, token, product_id, package_name, device, state, ts, consumed_at) "
                        "values(?,?,?,?,?,?,?,?,?)",
                        (token_hash, info["order_id"], None if state == "spent" else token,
                         playbilling.product_id(), playbilling.package_name(), device, state, now_s,
                         now_s if state == "spent" else None))
                    con.execute("release savepoint play_insert")
                except Exception as e:
                    con.execute("rollback to savepoint play_insert")
                    con.execute("release savepoint play_insert")
                    if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                        reject = PlayError(
                            "payment_used",
                            "That purchase was already used for a question. Each new question is $0.99.")
                    else:
                        raise
                if state == "spent" and reject is None:
                    reject = PlayError(
                        "payment_used",
                        "That purchase was already used for a question. Each new question is $0.99.")
        else:
            owner, state, ts, consumed_at = row
            if owner != device or state == "spent":
                reject = PlayError(
                    "payment_used",
                    "That purchase was already used for a question. Each new question is $0.99.")
                if state == "spent" and not consumed_at:
                    # Consumed below, outside the database lock.
                    pass
            elif state == "reserved" and ts >= stale_before:
                reject = PlayError(
                    "payment_in_progress",
                    "That purchase is already being used for a question. Wait a moment, then try again. You were not charged again.")
            elif info["consumption_state"] == 1:
                con.execute(
                    "update play_purchases set state='spent', consumed_at=?, token=null, ts=? where token_hash=?",
                    (now_s, now_s, token_hash))
                reject = PlayError(
                    "payment_used",
                    "That purchase was already used for a question. Each new question is $0.99.")
            else:
                con.execute(
                    "update play_purchases set state='reserved', token=?, ts=? where token_hash=? and device=?",
                    (token, now_s, token_hash, device))
        if reject is None and info["consumption_state"] == 1:
            reject = PlayError(
                "payment_used",
                "That purchase was already used for a question. Each new question is $0.99.")
        leftover = None
        if reject is not None and reject.code == "payment_used":
            leftover = con.execute(
                "select token from play_purchases where token_hash=? and state='spent' and consumed_at is null",
                (token_hash,)).fetchone()
    if leftover and leftover[0]:
        _consume_now(token_hash, leftover[0])
    if reject is not None:
        raise reject
    return token_hash


def finish_purchase(token_hash: str):
    """The answer started. This purchase cannot pay for a second question."""
    token = None
    with _lock, db.connect() as con:
        row = con.execute(
            "select device, token, state, consumed_at from play_purchases where token_hash=?",
            (token_hash,)).fetchone()
        if not row:
            return
        device, token, state, consumed_at = row
        now = datetime.now(timezone.utc).isoformat()
        if state != "spent":
            con.execute("update play_purchases set state='spent', ts=? where token_hash=?", (now, token_hash))
            con.execute(
                "insert into ledger(device, ts, delta, reason) values(?,?,?,?)",
                (device, now, 0, "acharya question"))
        if consumed_at:
            token = None
    _consume_now(token_hash, token)


def release_purchase(token_hash: str):
    """The answer never started. The same purchase may be used once more."""
    with _lock, db.connect() as con:
        row = con.execute(
            "select device, state from play_purchases where token_hash=?", (token_hash,)).fetchone()
        if not row or row[1] != "reserved":
            return
        now = datetime.now(timezone.utc).isoformat()
        con.execute(
            "update play_purchases set state='released', ts=? where token_hash=? and state='reserved'",
            (now, token_hash))
        con.execute(
            "insert into ledger(device, ts, delta, reason) values(?,?,?,?)",
            (row[0], now, 0, "acharya question released"))


def referral_info(device: str) -> dict:
    digest = hashlib.sha256(("drishti-ref-v1:" + device).encode()).hexdigest().upper()
    with _lock, db.connect() as con:
        row = con.execute("select code from referral_codes where device=?", (device,)).fetchone()
        if row:
            code = row[0]
        else:
            code = None
            for n in range(0, 8):
                candidate = digest[n:n + 8]
                taken = con.execute("select device from referral_codes where code=?", (candidate,)).fetchone()
                if taken:
                    continue
                con.execute("insert into referral_codes(device, code) values(?,?)", (device, candidate))
                code = candidate
                break
            if not code:
                raise HTTPException(500, "could not assign a referral code")
    return {"code": code, "bonus": 0, "path": "/?ref=" + code}


def attach_referral(device: str, code: str) -> dict:
    code = (code or "").strip().upper()
    if not re_code(code):
        return {"ok": False, "reason": "invalid"}
    with _lock, db.connect() as con:
        _ensure_wallet(con, device)
        row = con.execute("select device from referral_codes where code=?", (code,)).fetchone()
        if not row or row[0] == device:
            return {"ok": False, "reason": "invalid"}
        if con.execute("select 1 from referral_grants where invitee=?", (device,)).fetchone():
            return {"ok": False, "reason": "already"}
        used = con.execute("select used_free from wallet where device=?", (device,)).fetchone()[0]
        asked = con.execute(
            "select 1 from ledger where device=? and reason in ('free question', 'question', 'acharya question')",
            (device,)).fetchone()
        if used or asked:
            return {"ok": False, "reason": "not_new"}
        con.execute(
            "insert into referral_pending(device, code) values(?,?) on conflict(device) do update set code=excluded.code",
            (device, code))
    # An invite never includes a question. Each question is a separate $0.99 purchase.
    return {"ok": True, "bonus": 0}


def re_code(code: str) -> bool:
    return bool(code) and len(code) <= 16 and code.isalnum()


def deliver_magic_link(email: str, token: str) -> bool:
    """Send the link only when SMTP_HOST is set. Otherwise the caller reports a TODO and does not reveal the token."""
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return False
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", "").strip() or user or "noreply@localhost"
    base = os.environ.get("APP_PUBLIC_URL", "").rstrip("/")
    link = f"{base}/?magic={token}" if base else f"?magic={token}"
    msg = EmailMessage()
    msg["Subject"] = "Your Drishti sign-in link"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        "Sign in to Drishti. This link expires in 30 minutes and can be used once.\n\n"
        f"{link}\n")
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if port != 25:
            smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)
    return True


class OtpProvider:
    """Phone OTP boundary. No SMS vendor is wired.

    TODO: implement send/verify with an SMS provider when its key is available.
    """

    def send(self, phone: str) -> bool:
        raise NotImplementedError

    def verify(self, phone: str, code: str) -> bool:
        raise NotImplementedError


# -------------------------------------------------------------- cities ----
@lru_cache(maxsize=1)
def cities():
    rows = []
    with open(os.path.join(ROOT, "data", "cities.tsv"), encoding="utf-8") as f:
        for line in f:
            name, ascii_, admin, country, cc, lat, lon, tz, pop, alt = line.rstrip("\n").split("\t")
            keys = [ascii_.lower(), name.lower()] + [a.lower() for a in alt.split("|") if a]
            rows.append((keys, {"name": name, "admin": admin, "country": country, "cc": cc,
                                "lat": float(lat), "lon": float(lon), "tz": tz, "pop": int(pop or 0)}))
    rows.sort(key=lambda r: -r[1]["pop"])
    return rows


@app.get("/api/cities")
def city_search(q: str, limit: int = 8):
    q = q.strip().lower()
    if len(q) < 2:
        return []
    city_q, _, region_q = q.partition(",")
    region_q = region_q.strip()
    out = []
    for keys, c in cities():
        if any(k.startswith(city_q.strip()) for k in keys):
            if region_q and not (c["admin"].lower().startswith(region_q) or c["country"].lower().startswith(region_q)
                                 or c["cc"].lower() == region_q):
                continue
            out.append(c)
            if len(out) >= limit:
                break
    return out


# --------------------------------------------------------------- chart ----
class Place(BaseModel):
    name: str = ""
    lat: float
    lon: float
    tz: str = "UTC"


class Profile(BaseModel):
    name: str = "Friend"
    gender: str | None = None
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str | None = None
    place: Place
    offset: float | None = None  # manual UTC offset override (hours)


def chart_for(p: Profile, now: datetime | None = None) -> dict:
    try:
        utc, tzinfo = engine.local_to_utc(p.date, p.time, p.place.tz, p.place.lon, p.offset)
    except Exception as e:  # bad tz / date
        raise HTTPException(400, f"Invalid birth data: {e}")
    c = engine.compute_chart(utc, p.place.lat, p.place.lon, time_known=bool(p.time), now=now)
    c["timezone"] = tzinfo
    c["profile"] = {"name": p.name, "date": p.date, "time": p.time, "place": p.place.name}
    return c


@app.post("/api/chart")
def api_chart(p: Profile):
    return chart_for(p)


@app.get("/api/panchang")
def api_panchang(lat: float, lon: float, tz: str = "Asia/Kolkata", date: str | None = None):
    """Tithi, nakshatra, yoga, karana, vaar, sunrise/sunset and Rahu kaal for a place."""
    import panchang
    from zoneinfo import ZoneInfo
    if date is None:
        try:
            date = datetime.now(ZoneInfo(tz)).date().isoformat()
        except Exception as e:
            raise HTTPException(400, f"Invalid time zone: {e}")
    try:
        return panchang.panchang(date, lat, lon, tz)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/offset")
def api_offset(date: str, tz: str, lon: float, time: str | None = None):
    """Preview the UTC offset that will be used for a birth date/time/place."""
    try:
        utc, info = engine.local_to_utc(date, time or None, tz, lon)
    except Exception as e:
        raise HTTPException(400, str(e))
    return info | {"utc": utc.isoformat()}


class MatchReq(BaseModel):
    girl: Profile
    boy: Profile


@app.post("/api/match")
def api_match(m: MatchReq):
    g, b = chart_for(m.girl), chart_for(m.boy)
    return milan(g, b) | {"girl_name": m.girl.name, "boy_name": m.boy.name}


# ------------------------------------------------------------- status -----
@app.get("/api/status")
def status():
    c = llm.config()
    return {"app": APP_NAME, "ai_configured": llm.configured(), "model": c["model"] if llm.configured() else None,
            "referral_bonus": 0} | billing_public()


@app.get("/api/wallet")
def api_wallet(device: str):
    return wallet(device)


class Topup(BaseModel):
    device: str = ""
    pack: str = ""


@app.post("/api/wallet/topup")
def api_topup(t: Topup):
    """Credit packs are not sold. A question is one Play consumable."""
    raise HTTPException(410, "Credit packs are not sold. Each Acharya question is $0.99 on Google Play.")


class OrderReq(BaseModel):
    device: str = ""
    pack: str = ""


@app.post("/api/wallet/order")
def api_order(t: OrderReq):
    """No Razorpay order and no bundle. Questions are Play consumables."""
    raise HTTPException(410, "Credit packs are not sold. Each Acharya question is $0.99 on Google Play.")


# --------------------------------------------------------------- chat -----
class Msg(BaseModel):
    role: str
    content: str


class ChatReq(BaseModel):
    device: str
    profile: Profile
    messages: list[Msg]
    lang: str = "en"
    purchase_token: str | None = Field(default=None, max_length=4096)


def sse(obj) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _sse_error(code: str, message: str):
    async def gen():
        yield sse({"type": "error", "code": code, "message": message, "wallet": None})
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/chat")
async def api_chat(r: ChatReq):
    lang = r.lang if r.lang in llm.LANG_RULES else "en"
    # Refuse before taking a purchase when the astrologer cannot answer.
    if not llm.configured():
        return _sse_error(
            "ai_not_configured",
            "AI not configured yet. The astrologer will answer once an LLM API key is added on the server. You were not charged.")
    if not (r.purchase_token or "").strip():
        return _sse_error(
            "payment_required",
            "Each Acharya question is $0.99, paid through Google Play. This question was not answered. "
            "The chart, periods, dosha, panchang, matching, horoscope and kundli PDF stay free.")
    try:
        redemption = redeem_purchase(r.device, r.purchase_token or "")
    except PlayError as e:
        return _sse_error(e.code, e.message)
    try:
        chart = chart_for(r.profile)
    except HTTPException:
        release_purchase(redemption)
        raise
    ctx = llm.compact_chart(r.profile.model_dump() | {"place": r.profile.place.name}, chart)
    system = llm.SYSTEM_PROMPT.format(app=APP_NAME, lang_rule=llm.LANG_RULES[lang])
    facts = llm.chart_facts(ctx)
    msgs = [{"role": "system", "content": system},
            {"role": "system", "content": f"Today is {ctx['as_of']} UTC. {facts}\n"
             "Earlier chat messages are not a chart. Copy dasha periods only from the line above and vimshottari.cite_only, "
             "and transit sign changes only from the line above and gochar.ingresses.\nChart JSON:\n"
             + json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))}]
    for m in r.messages[-10:]:
        if m.role in ("user", "assistant"):
            msgs.append({"role": m.role, "content": m.content[:4000]})

    async def gen():
        got = False
        settled = False
        try:
            async for tok in llm.stream_chat(msgs):
                got = True
                yield sse({"type": "token", "text": tok})
            finish_purchase(redemption)
            settled = True
            yield sse({"type": "done", "charged": "play", "price": QUESTION_PRICE_LABEL, "wallet": wallet(r.device)})
        except Exception as e:
            if not settled:
                if got:
                    finish_purchase(redemption)
                else:
                    release_purchase(redemption)
                settled = True
            yield sse({"type": "error", "code": "llm_error",
                       "message": "The answer could not be completed. " + (
                           "This purchase was used because an answer had already started." if got
                           else "You were not charged again. Ask once more with the same purchase."),
                       "detail": str(e)[:160]})
        finally:
            if not settled:
                if got:
                    finish_purchase(redemption)
                else:
                    release_purchase(redemption)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------- horoscope ----
def _sign_label(sign: str, lang: str) -> str:
    """The name the reading must use: English rashi, or Devanagari when lang is hi."""
    if lang == "hi":
        return engine.SIGNS_HI[engine.SIGNS.index(sign)]
    return sign


def reading_fits(text: str | None, sign: str, lang: str) -> bool:
    """A cached paragraph belongs to this rashi only when it names that rashi in this language."""
    return bool(text) and _sign_label(sign, lang) in text


def _horo_json(payload: dict) -> JSONResponse:
    # no-store: a shared cache must not replay one sign's body for another query.
    return JSONResponse(payload, headers={"Cache-Control": "private, no-store"})


@app.get("/api/horoscope")
async def api_horoscope(sign: str, lang: str = "en"):
    if sign not in engine.SIGNS:
        raise HTTPException(400, "unknown sign")
    lang = lang if lang in llm.LANG_RULES else "en"
    now = datetime.now(timezone.utc)
    s = engine.SIGNS.index(sign)
    tr = engine.transits(now, s, s)
    facts = {p: {"sign": v["sign"], "house_from_rashi": v["house_from_moon"], "retrograde": v["retrograde"]}
             for p, v in tr["planets"].items()}
    day = now.date().isoformat()
    base = {"sign": sign, "lang": lang, "date": day, "transits": facts}
    if not llm.configured():
        return _horo_json(base | {"ai": False, "text": None})
    with db.connect() as con:
        row = con.execute("select body from horoscope where day=? and sign=? and lang=?", (day, sign, lang)).fetchone()
        if row and reading_fits(row[0], sign, lang):
            return _horo_json(base | {"ai": True, "text": row[0]})
        if row:
            con.execute("delete from horoscope where day=? and sign=? and lang=?", (day, sign, lang))
    try:
        text = await llm.complete([
            {"role": "system", "content": llm.HOROSCOPE_PROMPT.format(
                app=APP_NAME, sign=sign, sign_hi=engine.SIGNS_HI[s], lang_rule=llm.LANG_RULES[lang])},
            {"role": "user", "content": json.dumps(base, ensure_ascii=False)}], max_tokens=400)
    except Exception as e:
        return _horo_json(base | {"ai": False, "text": None, "error": str(e)[:160]})
    if not reading_fits(text, sign, lang):
        return _horo_json(base | {"ai": False, "text": None})
    with db.connect() as con:
        con.execute(
            "insert into horoscope(day, sign, lang, body) values(?,?,?,?) "
            "on conflict(day, sign, lang) do update set body=excluded.body",
            (day, sign, lang, text))
    return _horo_json(base | {"ai": True, "text": text})


# ------------------------------------------------------------- referral ----
@app.get("/api/referral")
def api_referral(device: str):
    if not device.strip():
        raise HTTPException(400, "device required")
    return referral_info(device)


class RefAttach(BaseModel):
    device: str
    code: str


@app.post("/api/referral/attach")
def api_attach(r: RefAttach):
    return attach_referral(r.device, r.code)


# ---------------------------------------------------------------- auth ----
class MagicReq(BaseModel):
    email: str
    device: str


class OtpReq(BaseModel):
    phone: str
    device: str


@app.post("/api/auth/magic")
def api_magic(r: MagicReq):
    email = r.email.strip().lower()
    if "@" not in email or len(email) > 200 or not r.device.strip():
        raise HTTPException(400, "valid email and device required")
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    with _lock, db.connect() as con:
        con.execute("insert into magic_links(token, email, device, expires, used) values(?,?,?,?,0)",
                    (token, email, r.device, expires))
    try:
        sent = deliver_magic_link(email, token)
    except Exception:
        sent = False
    detail = ("Check your email for a sign-in link." if sent else
              "TODO: email delivery needs SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM and APP_PUBLIC_URL. "
              "The link was stored but not sent, and it is not returned by this API.")
    return {"ok": True, "delivered": sent, "detail": detail}


@app.get("/api/auth/verify")
def api_verify(token: str, device: str):
    now = datetime.now(timezone.utc)
    with _lock, db.connect() as con:
        row = con.execute("select email, expires, used from magic_links where token=?", (token,)).fetchone()
        if not row:
            raise HTTPException(400, "unknown or used sign-in link")
        email, expires, used = row
        try:
            exp = datetime.fromisoformat(expires)
        except ValueError:
            exp = now
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if used or exp < now:
            raise HTTPException(400, "unknown or used sign-in link")
        con.execute("update magic_links set used=1 where token=?", (token,))
        created = now.isoformat()
        con.execute("insert into accounts(email, created) values(?,?) on conflict(email) do nothing", (email, created))
        _ensure_wallet(con, device)
        con.execute("update wallet set email=? where device=?", (email, device))
        session = secrets.token_urlsafe(24)
        con.execute("insert into sessions(token, email, device, created) values(?,?,?,?)",
                    (session, email, device, created))
        _adopt_device(con, email, device)
    return {"ok": True, "email": email, "session": session}


@app.get("/api/auth/me")
def api_me(device: str):
    with _lock, db.connect() as con:
        row = con.execute("select email from wallet where device=?", (device,)).fetchone()
    return {"email": row[0] if row else None}


def _owner(con, device: str, session: str | None) -> tuple[str, str | None]:
    """Account email when the session is valid, otherwise this device."""
    token = (session or "").strip()
    if token:
        row = con.execute("select email from sessions where token=?", (token,)).fetchone()
        if row:
            return "email:" + row[0], row[0]
    return "device:" + device.strip(), None


def _clean_profile(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    date = str(raw.get("date") or "").strip()
    if not pid or len(pid) > 80 or not name or len(name) > 80:
        return None
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None
    place = raw.get("place") if isinstance(raw.get("place"), dict) else None
    if not place:
        return None
    try:
        lat, lon = float(place.get("lat")), float(place.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    gender = raw.get("gender")
    if gender not in ("Female", "Male", "Other", None):
        gender = None
    time = raw.get("time")
    if time is not None:
        time = str(time).strip()
        if not time or len(time) > 8:
            time = None
    offset = raw.get("offset")
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        offset = None
    return {
        "id": pid, "name": name, "relation": str(raw.get("relation") or "Self")[:40],
        "gender": gender, "date": date, "time": time,
        "place": {"name": str(place.get("name") or "")[:160], "lat": lat, "lon": lon,
                  "tz": str(place.get("tz") or "Asia/Kolkata")[:80]},
        "offset": offset,
    }


def _clean_messages(raw) -> list:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[-40:]:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant", "error"):
            continue
        msg = {"role": item["role"], "content": str(item.get("content") or "")[:8000]}
        if msg["role"] == "error":
            msg["code"] = str(item.get("code") or "llm_error")[:40]
        out.append(msg)
    return out


def _adopt_device(con, email: str, device: str):
    """On first sign-in, the charts saved for this browser become the account's."""
    owner, dev = "email:" + email, "device:" + device.strip()
    if con.execute("select 1 from profiles where owner=?", (owner,)).fetchone():
        return
    con.execute("update profiles set owner=? where owner=?", (owner, dev))
    con.execute("update chats set owner=? where owner=?", (owner, dev))
    if not con.execute("select 1 from account_state where owner=?", (owner,)).fetchone():
        con.execute("update account_state set owner=? where owner=?", (owner, dev))


def _snapshot(con, owner: str) -> dict:
    profiles = []
    for pid, body in con.execute("select id, body from profiles where owner=? order by updated", (owner,)).fetchall():
        try:
            profiles.append(json.loads(body))
        except json.JSONDecodeError:
            continue
    chats = {}
    for pid, body in con.execute("select profile_id, body from chats where owner=?", (owner,)).fetchall():
        try:
            chats[pid] = json.loads(body)
        except json.JSONDecodeError:
            continue
    state = con.execute("select active from account_state where owner=?", (owner,)).fetchone()
    email = owner[6:] if owner.startswith("email:") else None
    return {"storage": con.dialect, "owner": "account" if email else "device", "email": email,
            "profiles": profiles, "active": state[0] if state else None, "chats": chats}


def _save_snapshot(con, owner: str, profiles: list[dict], active: str | None, chats: dict) -> dict:
    cleaned = []
    for raw in profiles[:20]:
        item = _clean_profile(raw)
        if item:
            cleaned.append(item)
    ids = [p["id"] for p in cleaned]
    now = datetime.now(timezone.utc).isoformat()
    if ids:
        marks = ",".join("?" * len(ids))
        con.execute(f"delete from profiles where owner=? and id not in ({marks})", (owner, *ids))
        con.execute(f"delete from chats where owner=? and profile_id not in ({marks})", (owner, *ids))
    else:
        con.execute("delete from profiles where owner=?", (owner,))
        con.execute("delete from chats where owner=?", (owner,))
    for item in cleaned:
        owned = con.execute("select owner from profiles where id=?", (item["id"],)).fetchone()
        if owned and owned[0] != owner:
            continue
        con.execute(
            "insert into profiles(id, owner, body, updated) values(?,?,?,?) "
            "on conflict(id) do update set owner=excluded.owner, body=excluded.body, updated=excluded.updated",
            (item["id"], owner, json.dumps(item, ensure_ascii=False), now))
    kept = {row[0] for row in con.execute("select id from profiles where owner=?", (owner,)).fetchall()}
    for pid in list(kept):
        msgs = _clean_messages(chats.get(pid)) if isinstance(chats, dict) else []
        if msgs:
            con.execute(
                "insert into chats(owner, profile_id, body, updated) values(?,?,?,?) "
                "on conflict(owner, profile_id) do update set body=excluded.body, updated=excluded.updated",
                (owner, pid, json.dumps(msgs, ensure_ascii=False), now))
        else:
            con.execute("delete from chats where owner=? and profile_id=?", (owner, pid))
    chosen = active if active in kept else (ids[0] if ids else None)
    con.execute(
        "insert into account_state(owner, active, updated) values(?,?,?) "
        "on conflict(owner) do update set active=excluded.active, updated=excluded.updated",
        (owner, chosen, now))
    return _snapshot(con, owner)


class SyncBody(BaseModel):
    device: str
    session: str | None = None
    profiles: list[dict] = Field(default_factory=list)
    active: str | None = None
    chats: dict[str, list] = Field(default_factory=dict)


@app.get("/api/sync")
def api_sync_get(device: str, session: str | None = None):
    if not device.strip():
        raise HTTPException(400, "device required")
    with _lock, db.connect() as con:
        owner, _email = _owner(con, device, session)
        return _snapshot(con, owner)


@app.post("/api/sync")
def api_sync_put(body: SyncBody):
    if not body.device.strip():
        raise HTTPException(400, "device required")
    with _lock, db.connect() as con:
        owner, _email = _owner(con, body.device, body.session)
        return _save_snapshot(con, owner, body.profiles, body.active, body.chats)


@app.post("/api/auth/otp")
def api_otp(r: OtpReq):
    # OtpProvider is the seam. Nothing sends an SMS until a vendor is configured.
    _ = OtpProvider
    raise HTTPException(
        501,
        "Phone OTP is not configured. TODO: implement OtpProvider.send and OtpProvider.verify with an SMS vendor. "
        "Email magic link is POST /api/auth/magic.")


# -------------------------------------------------------- gita verse ----
@app.get("/api/gita")
def api_gita(date: str | None = None):
    """Today's shloka. No login. `date` is the device's local YYYY-MM-DD."""
    if date is not None and date.strip():
        try:
            day = datetime.strptime(date.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD")
    else:
        day = datetime.now(timezone.utc).date()
    return JSONResponse(gita.verse_for(day), headers={"Cache-Control": "public, max-age=3600"})


# ------------------------------------------------------------------ pdf ----
@app.post("/api/kundli.pdf")
def api_kundli_pdf(p: Profile, lang: str = "en"):
    import pdfkundli
    chart = chart_for(p)
    try:
        data = pdfkundli.render_pdf(chart, lang)
    except Exception as e:
        raise HTTPException(500, f"Could not build the PDF: {e}")
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="drishti-kundli.pdf"'})


# -------------------------------------------------------------- static ----
_SHA256_PREFIX = re.compile(r"(?i)^sha-?256\s*[:=]\s*")


def normalize_sha256_fingerprint(value: str) -> str | None:
    """Turn a keytool or Play Console fingerprint into AA:BB:… uppercase form."""
    text = _SHA256_PREFIX.sub("", (value or "").strip())
    hexes = re.sub(r"[^0-9a-fA-F]", "", text)
    if len(hexes) != 64:
        return None
    return ":".join(hexes[i:i + 2].upper() for i in range(0, 64, 2))


def asset_link_fingerprints() -> list[str]:
    raw = os.environ.get("ANDROID_SHA256_FINGERPRINTS", "")
    found: list[str] = []
    for part in raw.split(","):
        fp = normalize_sha256_fingerprint(part)
        if fp and fp not in found:
            found.append(fp)
    return found


@app.get("/.well-known/assetlinks.json")
def assetlinks():
    """Digital Asset Links so the Android wrapper can hide the URL bar.

    Fingerprints come from ANDROID_SHA256_FINGERPRINTS (comma-separated).
    Until that is set, the file is an empty list and the wrapper shows Chrome's bar.
    """
    prints = asset_link_fingerprints()
    body = []
    if prints:
        body.append({
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "com.ikshana.drishti",
                "sha256_cert_fingerprints": prints,
            },
        })
    return JSONResponse(body, headers={"Cache-Control": "public, max-age=300"})


@app.get("/")
def index():
    """Home page. The verse list is inlined so the splash can paint before any fetch."""
    with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(list(gita.verses()), ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    html = html.replace("window.__GITA_INJECTED__", payload)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/privacy")
def privacy():
    return FileResponse(os.path.join(WEB, "privacy.html"))


app.mount("/", StaticFiles(directory=WEB), name="web")
