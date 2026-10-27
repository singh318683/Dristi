"""Drishti backend: chart engine API, city search, AI chat (SSE), matching, horoscope, wallet stub."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import smtplib
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import engine
import llm
from matching import milan

APP_NAME = "Drishti"
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(ROOT), "web")
FREE_QUESTIONS = int(os.environ.get("FREE_QUESTIONS", "3"))
QUESTION_COST = int(os.environ.get("QUESTION_COST", "15"))  # credits per question after free quota
PACKS = {"p100": {"credits": 100, "price": "₹99"}, "p300": {"credits": 330, "price": "₹249"},
         "p1000": {"credits": 1200, "price": "₹699"}}


def referral_bonus() -> int:
    return int(os.environ.get("REFERRAL_BONUS", "15"))


def payment_provider() -> str:
    """'razorpay' only when both keys exist. Charging is still not implemented."""
    if os.environ.get("RAZORPAY_KEY_ID", "").strip() and os.environ.get("RAZORPAY_KEY_SECRET", "").strip():
        return "razorpay"
    return "demo"

app = FastAPI(title=APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ------------------------------------------------------------------ db ----
_lock = threading.Lock()


def _ensure_wallet(con, device: str):
    con.execute("insert into wallet(device) values(?) on conflict(device) do nothing", (device,))


def wallet(device: str) -> dict:
    with _lock, db.connect() as con:
        _ensure_wallet(con, device)
        c, u, email = con.execute("select credits, used_free, email from wallet where device=?", (device,)).fetchone()
    return {"credits": c, "free_left": max(0, FREE_QUESTIONS - u), "free_total": FREE_QUESTIONS,
            "question_cost": QUESTION_COST, "packs": PACKS, "email": email,
            "payment": payment_provider()}


def _grant_referral(con, device: str, used_free: int):
    """On the invitee's first charged question, credit both devices once."""
    if used_free > 0:
        return
    if con.execute("select 1 from referral_grants where invitee=?", (device,)).fetchone():
        return
    pend = con.execute("select code from referral_pending where device=?", (device,)).fetchone()
    if not pend:
        return
    ref = con.execute("select device from referral_codes where code=?", (pend[0],)).fetchone()
    if not ref or ref[0] == device:
        return
    bonus = referral_bonus()
    now = datetime.now(timezone.utc).isoformat()
    for who in (device, ref[0]):
        _ensure_wallet(con, who)
        con.execute("update wallet set credits = credits + ? where device=?", (bonus, who))
        con.execute("insert into ledger(device, ts, delta, reason) values(?,?,?,?)",
                    (who, now, bonus, "referral bonus"))
    con.execute("insert into referral_grants(invitee, referrer, code, ts) values(?,?,?,?)",
                (device, ref[0], pend[0], now))


def charge(device: str) -> str | None:
    """Reserve one question. Returns 'free' / 'paid' or None if insufficient."""
    with _lock, db.connect() as con:
        _ensure_wallet(con, device)
        c, u = con.execute("select credits, used_free from wallet where device=?", (device,)).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if u < FREE_QUESTIONS:
            _grant_referral(con, device, u)
            con.execute("update wallet set used_free=used_free+1 where device=?", (device,))
            con.execute("insert into ledger(device, ts, delta, reason) values(?,?,?,?)", (device, now, 0, "free question"))
            return "free"
        if c >= QUESTION_COST:
            _grant_referral(con, device, u)
            con.execute("update wallet set credits=credits-? where device=?", (QUESTION_COST, device))
            con.execute("insert into ledger(device, ts, delta, reason) values(?,?,?,?)",
                        (device, now, -QUESTION_COST, "question"))
            return "paid"
    return None


def refund(device: str, kind: str):
    with _lock, db.connect() as con:
        if kind == "free":
            con.execute("update wallet set used_free=max(0,used_free-1) where device=?", (device,))
        else:
            con.execute("update wallet set credits=credits+? where device=?", (QUESTION_COST, device))
        con.execute("insert into ledger(device, ts, delta, reason) values(?,?,?,?)",
                    (device, datetime.now(timezone.utc).isoformat(),
                     0 if kind == "free" else QUESTION_COST, "refund (AI error)"))


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
    return {"code": code, "bonus": referral_bonus(), "path": "/?ref=" + code}


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
            "select 1 from ledger where device=? and reason in ('free question', 'question')", (device,)).fetchone()
        if used or asked:
            return {"ok": False, "reason": "not_new"}
        con.execute(
            "insert into referral_pending(device, code) values(?,?) on conflict(device) do update set code=excluded.code",
            (device, code))
    return {"ok": True, "bonus": referral_bonus()}


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
            "free_questions": FREE_QUESTIONS, "question_cost": QUESTION_COST,
            "payment": payment_provider(), "referral_bonus": referral_bonus()}


@app.get("/api/wallet")
def api_wallet(device: str):
    return wallet(device)


class Topup(BaseModel):
    device: str
    pack: str


@app.post("/api/wallet/topup")
def api_topup(t: Topup):
    """STUB: no real payment. In production this is called by the payment webhook
    (Razorpay / Stripe / App Store / Play Billing) after verifying the receipt."""
    if t.pack not in PACKS:
        raise HTTPException(400, "unknown pack")
    with _lock, db.connect() as con:
        _ensure_wallet(con, t.device)
        con.execute("update wallet set credits=credits+? where device=?", (PACKS[t.pack]["credits"], t.device))
        con.execute("insert into ledger(device, ts, delta, reason) values(?,?,?,?)",
                    (t.device, datetime.now(timezone.utc).isoformat(),
                     PACKS[t.pack]["credits"], f"DEMO top-up {t.pack}"))
    return wallet(t.device) | {"demo": True, "provider": payment_provider()}


class OrderReq(BaseModel):
    device: str
    pack: str


@app.post("/api/wallet/order")
def api_order(t: OrderReq):
    """Razorpay-shaped stub. Does not create an order or add credits."""
    if t.pack not in PACKS:
        raise HTTPException(400, "unknown pack")
    if payment_provider() != "razorpay":
        raise HTTPException(
            501,
            "Razorpay is not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET. "
            "TODO: create a Razorpay order and credit the wallet only after the payment signature is verified. "
            "Until then, POST /api/wallet/topup adds demo credits and does not take payment.")
    raise HTTPException(
        501,
        "TODO: create a Razorpay order for this pack and return order_id. Do not credit the wallet in this request.")


# --------------------------------------------------------------- chat -----
class Msg(BaseModel):
    role: str
    content: str


class ChatReq(BaseModel):
    device: str
    profile: Profile
    messages: list[Msg]
    lang: str = "en"


def sse(obj) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def api_chat(r: ChatReq):
    lang = r.lang if r.lang in llm.LANG_RULES else "en"
    if not llm.configured():
        async def nokey():
            yield sse({"type": "error", "code": "ai_not_configured",
                       "message": "AI not configured yet. The astrologer will answer once an LLM API key is added on the server."})
        return StreamingResponse(nokey(), media_type="text/event-stream")
    kind = charge(r.device)
    if kind is None:
        async def broke():
            yield sse({"type": "error", "code": "insufficient_credits",
                       "message": f"You've used your free questions. Each question costs {QUESTION_COST} credits."})
        return StreamingResponse(broke(), media_type="text/event-stream")
    chart = chart_for(r.profile)
    ctx = llm.compact_chart(r.profile.model_dump() | {"place": r.profile.place.name}, chart)
    system = llm.SYSTEM_PROMPT.format(app=APP_NAME, lang_rule=llm.LANG_RULES[lang])
    msgs = [{"role": "system", "content": system},
            {"role": "system", "content": f"Today is {ctx['as_of']} UTC. Copy dasha periods only from vimshottari.cite_only, and transit sign changes only from gochar.ingresses.\nChart JSON:\n"
             + json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))}]
    for m in r.messages[-10:]:
        if m.role in ("user", "assistant"):
            msgs.append({"role": m.role, "content": m.content[:4000]})

    async def gen():
        got = False
        try:
            async for tok in llm.stream_chat(msgs):
                got = True
                yield sse({"type": "token", "text": tok})
            yield sse({"type": "done", "charged": kind, "wallet": wallet(r.device)})
        except Exception as e:
            if not got:
                refund(r.device, kind)
            yield sse({"type": "error", "code": "llm_error", "message": f"The astrologer is unavailable right now ({str(e)[:160]})."})
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------- horoscope ----
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
    base = {"sign": sign, "date": now.date().isoformat(), "transits": facts}
    if not llm.configured():
        return base | {"ai": False, "text": None}
    day = now.date().isoformat()
    with db.connect() as con:
        row = con.execute("select body from horoscope where day=? and sign=? and lang=?", (day, sign, lang)).fetchone()
    if row:
        return base | {"ai": True, "text": row[0]}
    try:
        text = await llm.complete([
            {"role": "system", "content": llm.HOROSCOPE_PROMPT.format(app=APP_NAME, sign=sign, lang_rule=llm.LANG_RULES[lang])},
            {"role": "user", "content": json.dumps(base, ensure_ascii=False)}], max_tokens=400)
    except Exception as e:
        return base | {"ai": False, "text": None, "error": str(e)[:160]}
    with db.connect() as con:
        con.execute(
            "insert into horoscope(day, sign, lang, body) values(?,?,?,?) "
            "on conflict(day, sign, lang) do update set body=excluded.body",
            (day, sign, lang, text))
    return base | {"ai": True, "text": text}


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
    return {"ok": True, "email": email, "session": session}


@app.get("/api/auth/me")
def api_me(device: str):
    with _lock, db.connect() as con:
        row = con.execute("select email from wallet where device=?", (device,)).fetchone()
    return {"email": row[0] if row else None}


@app.post("/api/auth/otp")
def api_otp(r: OtpReq):
    # OtpProvider is the seam. Nothing sends an SMS until a vendor is configured.
    _ = OtpProvider
    raise HTTPException(
        501,
        "Phone OTP is not configured. TODO: implement OtpProvider.send and OtpProvider.verify with an SMS vendor. "
        "Email magic link is POST /api/auth/magic.")


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
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/privacy")
def privacy():
    return FileResponse(os.path.join(WEB, "privacy.html"))


app.mount("/", StaticFiles(directory=WEB), name="web")
