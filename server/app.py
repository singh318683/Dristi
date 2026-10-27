"""Drishti backend: chart engine API, city search, AI chat (SSE), matching, horoscope, wallet stub."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import engine
import llm
from matching import milan

APP_NAME = "Drishti"
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(ROOT), "web")
DB = os.environ.get("DRISHTI_DB", os.path.join(ROOT, "data", "app.db"))
FREE_QUESTIONS = int(os.environ.get("FREE_QUESTIONS", "3"))
QUESTION_COST = int(os.environ.get("QUESTION_COST", "15"))  # credits per question after free quota
PACKS = {"p100": {"credits": 100, "price": "₹99"}, "p300": {"credits": 330, "price": "₹249"},
         "p1000": {"credits": 1200, "price": "₹699"}}

app = FastAPI(title=APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ------------------------------------------------------------------ db ----
_lock = threading.Lock()


def db():
    con = sqlite3.connect(DB)
    con.execute("create table if not exists wallet(device text primary key, credits int default 0, used_free int default 0)")
    con.execute("create table if not exists ledger(device text, ts text, delta int, reason text)")
    con.execute("create table if not exists horoscope(day text, sign text, lang text, body text, primary key(day,sign,lang))")
    return con


def wallet(device: str) -> dict:
    with _lock, db() as con:
        con.execute("insert or ignore into wallet(device) values(?)", (device,))
        c, u = con.execute("select credits, used_free from wallet where device=?", (device,)).fetchone()
    return {"credits": c, "free_left": max(0, FREE_QUESTIONS - u), "free_total": FREE_QUESTIONS,
            "question_cost": QUESTION_COST, "packs": PACKS}


def charge(device: str) -> str | None:
    """Reserve one question. Returns 'free' / 'paid' or None if insufficient."""
    with _lock, db() as con:
        con.execute("insert or ignore into wallet(device) values(?)", (device,))
        c, u = con.execute("select credits, used_free from wallet where device=?", (device,)).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if u < FREE_QUESTIONS:
            con.execute("update wallet set used_free=used_free+1 where device=?", (device,))
            con.execute("insert into ledger values(?,?,?,?)", (device, now, 0, "free question"))
            return "free"
        if c >= QUESTION_COST:
            con.execute("update wallet set credits=credits-? where device=?", (QUESTION_COST, device))
            con.execute("insert into ledger values(?,?,?,?)", (device, now, -QUESTION_COST, "question"))
            return "paid"
    return None


def refund(device: str, kind: str):
    with _lock, db() as con:
        if kind == "free":
            con.execute("update wallet set used_free=max(0,used_free-1) where device=?", (device,))
        else:
            con.execute("update wallet set credits=credits+? where device=?", (QUESTION_COST, device))
        con.execute("insert into ledger values(?,?,?,?)", (device, datetime.now(timezone.utc).isoformat(),
                                                             0 if kind == "free" else QUESTION_COST, "refund (AI error)"))


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
            "free_questions": FREE_QUESTIONS, "question_cost": QUESTION_COST}


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
    with _lock, db() as con:
        con.execute("insert or ignore into wallet(device) values(?)", (t.device,))
        con.execute("update wallet set credits=credits+? where device=?", (PACKS[t.pack]["credits"], t.device))
        con.execute("insert into ledger values(?,?,?,?)", (t.device, datetime.now(timezone.utc).isoformat(),
                                                           PACKS[t.pack]["credits"], f"DEMO top-up {t.pack}"))
    return wallet(t.device) | {"demo": True}


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
    with db() as con:
        row = con.execute("select body from horoscope where day=? and sign=? and lang=?", (day, sign, lang)).fetchone()
    if row:
        return base | {"ai": True, "text": row[0]}
    try:
        text = await llm.complete([
            {"role": "system", "content": llm.HOROSCOPE_PROMPT.format(app=APP_NAME, sign=sign, lang_rule=llm.LANG_RULES[lang])},
            {"role": "user", "content": json.dumps(base, ensure_ascii=False)}], max_tokens=400)
    except Exception as e:
        return base | {"ai": False, "text": None, "error": str(e)[:160]}
    with db() as con:
        con.execute("insert or replace into horoscope values(?,?,?,?)", (day, sign, lang, text))
    return base | {"ai": True, "text": text}


# -------------------------------------------------------------- static ----
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/privacy")
def privacy():
    return FileResponse(os.path.join(WEB, "privacy.html"))


app.mount("/", StaticFiles(directory=WEB), name="web")
