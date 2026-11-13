"""HTTP API for personal astrologers. Off unless ASTROLOGER_CONNECT_ENABLED is set."""
from __future__ import annotations

import hmac
import os
import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

import connect_livekit
import connect_store as store
import connect_stripe
import db

router = APIRouter()


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def feature_on() -> bool:
    return _flag("ASTROLOGER_CONNECT_ENABLED")


def publishable_key() -> str:
    if not store_ready():
        return ""
    return (os.environ.get("STRIPE_PUBLISHABLE_KEY") or "").strip()


def store_ready() -> bool:
    if not feature_on():
        return False
    if db.dialect_of() == "postgres":
        return True
    return _flag("ASTROLOGER_CONNECT_ALLOW_SQLITE")


def _gate() -> None:
    if not feature_on():
        raise HTTPException(404, "connect_disabled")
    if not store_ready():
        raise HTTPException(503, "connect_unavailable")


def _secret_match(got: str, expected: str) -> bool:
    got = got or ""
    expected = expected or ""
    if not expected or len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)


def _admin(request: Request) -> None:
    _gate()
    token = (os.environ.get("ADMIN_TOKEN") or "").strip()
    header = request.headers.get("authorization") or ""
    bearer = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token or not _secret_match(bearer, token):
        raise HTTPException(401, "unauthorized")


def _astro_id(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    bearer = header[7:].strip() if header.lower().startswith("bearer ") else ""
    try:
        return store.astro_from_token(bearer)
    except store.ConnectError as e:
        raise HTTPException(e.status, e.code) from e


def _run(fn):
    try:
        return fn()
    except store.ConnectError as e:
        detail = {"error": e.code, **e.extra} if e.extra else e.code
        raise HTTPException(e.status, detail) from e


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str
    name: str
    specialties: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    years: int = 0
    bio: str = ""


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str


class OnlineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    online: bool


class QuoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str
    astrologer_id: str


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str
    astrologer_id: str
    mode: Literal["chat", "call"]
    question: str = ""
    profile: dict | None = None
    idempotency_key: str = ""


class DeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "astrologer"]
    device: str = ""


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sender: Literal["user", "astrologer"]
    body: str
    device: str = ""


class IntentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str
    product_id: str
    idempotency_key: str = ""


class AppleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str
    product_id: str
    transaction_id: str
    signed_transaction: str = ""


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str
    astrologer_id: str
    session_id: str = ""
    reason: str


class BlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str
    astrologer_id: str


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    rate_cents: int = 0
    rating_tenths: int = 0


@router.get("/api/connect/astrologers")
def api_list(device: str):
    _gate()
    return {"astrologers": _run(lambda: store.list_astrologers(device))}


@router.post("/api/connect/apply")
def api_apply(body: ApplyIn):
    _gate()
    return _run(lambda: store.apply_astrologer(body.email, body.password, body.name, body.specialties, body.languages, body.years, body.bio))


@router.post("/api/connect/astro/login")
def api_login(body: LoginIn):
    _gate()
    return _run(lambda: store.astro_login(body.email, body.password))


@router.post("/api/connect/astro/online")
def api_online(body: OnlineIn, request: Request):
    _gate()
    astro_id = _astro_id(request)
    return _run(lambda: store.set_online(astro_id, body.online))


@router.get("/api/connect/astro/incoming")
def api_incoming(request: Request):
    _gate()
    astro_id = _astro_id(request)
    return {"sessions": _run(lambda: store.incoming(astro_id))}


@router.get("/api/connect/astro/earnings")
def api_earnings(request: Request):
    _gate()
    astro_id = _astro_id(request)
    return _run(lambda: store.earnings(astro_id))


@router.post("/api/connect/astro/stripe")
def api_stripe_onboard(request: Request):
    _gate()
    astro_id = _astro_id(request)
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    public = (os.environ.get("APP_PUBLIC_URL") or "").strip().rstrip("/")
    if not secret or not public:
        raise HTTPException(503, "stripe_not_configured")
    info = _run(lambda: store.astrologer_private(astro_id))
    account = info["stripe_account"]
    if not account:
        created = connect_stripe.create_express_account(secret, info["email"], "acct-" + astro_id)
        account = created["id"]
        store.set_stripe_account(astro_id, account)
    link = connect_stripe.create_account_link(secret, account, public + "/#astro", public + "/#astro")
    return {"url": link.get("url") or ""}


@router.post("/api/connect/quote")
def api_quote(body: QuoteIn):
    _gate()
    return _run(lambda: store.quote(body.device, body.astrologer_id))


@router.get("/api/connect/wallet")
def api_wallet(device: str):
    _gate()
    return _run(lambda: store.wallet_view(device))


@router.post("/api/connect/wallet/intent")
def api_intent(body: IntentIn):
    _gate()
    import connect_money as money
    try:
        cents = money.product_cents(body.product_id)
    except money.MoneyError as e:
        raise HTTPException(400, e.code) from e
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not secret:
        raise HTTPException(503, "stripe_not_configured")
    nonce = (body.idempotency_key or "").strip()[:80] or secrets.token_hex(8)
    intent = connect_stripe.create_payment_intent(secret, cents, {
        "device": body.device,
        "product_id": body.product_id,
        "purpose": "wallet",
    }, "pi-" + nonce)
    return {"client_secret": intent.get("client_secret"), "product_id": body.product_id, "amount_cents": cents}


@router.post("/api/connect/wallet/apple")
def api_apple(body: AppleIn):
    _gate()
    import connect_storekit
    try:
        info = connect_storekit.verify_transaction(body.transaction_id, body.signed_transaction)
    except connect_storekit.StoreKitError as e:
        raise HTTPException(400, e.code) from e
    if info.get("product_id") != body.product_id:
        raise HTTPException(400, "payment_invalid")
    return _run(lambda: store.credit_topup(body.device, "apple", info["product_id"], info["transaction_id"]))


@router.post("/api/connect/stripe/webhook")
async def api_stripe_webhook(request: Request):
    _gate()
    secret = (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not secret:
        raise HTTPException(503, "stripe_not_configured")
    payload = await request.body()
    try:
        event = connect_stripe.verify_event(payload, request.headers.get("stripe-signature") or "", secret)
    except connect_stripe.StripeError as e:
        raise HTTPException(400, e.code) from e
    return apply_stripe_event(event)


def apply_stripe_event(event: dict) -> dict:
    if event.get("type") != "payment_intent.succeeded":
        return {"ok": True, "ignored": True}
    obj = (event.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    if meta.get("purpose") != "wallet":
        return {"ok": True, "ignored": True}
    payment_id = str(obj.get("id") or "")
    result = store.credit_topup(str(meta.get("device") or ""), "stripe", str(meta.get("product_id") or ""), payment_id)
    return {"ok": True, **result}


@router.post("/api/connect/sessions")
def api_start(body: SessionIn):
    _gate()
    return _run(lambda: store.start_session(body.device, body.astrologer_id, body.mode, body.question, body.profile, body.idempotency_key))


@router.get("/api/connect/sessions/{session_id}")
def api_session(session_id: str, request: Request, device: str = ""):
    _gate()
    astro_id = None
    header = request.headers.get("authorization") if request else ""
    if header:
        astro_id = _astro_id(request)
    view = _run(lambda: store.session_view(session_id))
    if device and device != view["device"] and astro_id != view["astrologer_id"]:
        raise HTTPException(401, "unauthorized")
    if not device and astro_id != view["astrologer_id"]:
        raise HTTPException(401, "unauthorized")
    return view


@router.post("/api/connect/sessions/{session_id}/accept")
def api_accept(session_id: str, request: Request):
    _gate()
    astro_id = _astro_id(request)
    return _run(lambda: store.accept_session(session_id, astro_id))


@router.post("/api/connect/sessions/{session_id}/heartbeat")
def api_heartbeat(session_id: str, body: HeartbeatIn, request: Request):
    _gate()
    astro_id = _astro_id(request) if body.role == "astrologer" else None
    return _run(lambda: store.heartbeat(session_id, body.role, body.device, astro_id))


@router.post("/api/connect/sessions/{session_id}/end")
def api_end(session_id: str, body: DeviceIn, request: Request):
    _gate()
    astro_id = None
    if request.headers.get("authorization"):
        astro_id = _astro_id(request)
    return _run(lambda: store.end_session(session_id, body.device, astro_id))


@router.post("/api/connect/sessions/{session_id}/messages")
def api_post_message(session_id: str, body: MessageIn, request: Request):
    _gate()
    astro_id = _astro_id(request) if body.sender == "astrologer" else None
    _run(lambda: store.post_message(session_id, body.sender, body.body, body.device, astro_id))
    return {"ok": True}


@router.get("/api/connect/sessions/{session_id}/messages")
def api_messages(session_id: str, request: Request, device: str = "", after: int = 0):
    _gate()
    astro_id = None
    if request and request.headers.get("authorization"):
        astro_id = _astro_id(request)
    return {"messages": _run(lambda: store.list_messages(session_id, after, device, astro_id))}


@router.post("/api/connect/sessions/{session_id}/call-token")
def api_call_token(session_id: str, body: DeviceIn, request: Request):
    _gate()
    astro_id = None
    if request.headers.get("authorization"):
        astro_id = _astro_id(request)
    return _run(lambda: store.issue_call(
        session_id, body.device, astro_id,
        (os.environ.get("LIVEKIT_API_KEY") or "").strip(),
        (os.environ.get("LIVEKIT_API_SECRET") or "").strip(),
        (os.environ.get("LIVEKIT_URL") or "").strip(),
    ))


@router.post("/api/connect/livekit/webhook")
async def api_livekit_webhook(request: Request):
    _gate()
    secret = (os.environ.get("LIVEKIT_API_SECRET") or "").strip()
    if not secret:
        raise HTTPException(503, "call_unavailable")
    payload = await request.body()
    try:
        connect_livekit.verify_webhook(request.headers.get("authorization") or "", payload, secret)
    except ValueError as e:
        raise HTTPException(401, "unauthorized") from e
    import json
    event = json.loads(payload)
    kind = event.get("event") or ""
    room = ((event.get("room") or {}).get("name")) or event.get("room") or ""
    if kind in ("participant_left", "room_finished") and room:
        store.end_room(str(room), "provider")
    return {"ok": True}


@router.post("/api/connect/report")
def api_report(body: ReportIn):
    _gate()
    return _run(lambda: store.report(body.device, body.astrologer_id, body.session_id, body.reason))


@router.post("/api/connect/block")
def api_block(body: BlockIn):
    _gate()
    return _run(lambda: store.block(body.device, body.astrologer_id))


@router.get("/api/connect/admin/astrologers")
def api_admin_list(request: Request):
    _admin(request)
    return {"astrologers": store.admin_list()}


@router.post("/api/connect/admin/astrologers/{astrologer_id}")
def api_admin_decide(astrologer_id: str, body: DecideIn, request: Request):
    _admin(request)
    return _run(lambda: store.admin_decide(astrologer_id, body.approve, body.rate_cents, body.rating_tenths))


@router.get("/api/cron/connect-payouts")
def api_cron(request: Request):
    secret = (os.environ.get("CRON_SECRET") or "").strip()
    header = request.headers.get("authorization") or ""
    bearer = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not _secret_match(bearer, secret):
        raise HTTPException(401, "unauthorized")
    if not store_ready():
        return {"ok": True, "skipped": "disabled"}
    stripe_key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not stripe_key:
        return {"ok": True, "skipped": "stripe_not_configured"}
    sent = []
    for row in store.payout_candidates():
        transfer = connect_stripe.create_transfer(stripe_key, row["amount_cents"], row["stripe_account"], "transfer-" + row["astrologer_id"])
        marked = store.mark_payout(row["astrologer_id"], row["amount_cents"], transfer.get("id") or "")
        sent.append(marked)
    return {"ok": True, "payouts": sent}
