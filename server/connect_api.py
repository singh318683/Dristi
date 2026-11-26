"""HTTP API for personal astrologers. Off unless ASTROLOGER_CONNECT_ENABLED is set."""
from __future__ import annotations

import hmac
import os
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

import connect_livekit
import connect_paypal
import connect_store as store
import connect_stripe
import db

APP_SHELLS = ("ios", "android")
# Server-to-server and admin calls carry no app shell header. They are never
# limited by ASTROLOGER_CONNECT_PLATFORMS.
_PLATFORM_EXEMPT = (
    "/api/connect/stripe/webhook",
    "/api/connect/livekit/webhook",
    "/api/connect/admin/",
)


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def feature_on() -> bool:
    return _flag("ASTROLOGER_CONNECT_ENABLED")


def app_shell(request: Request | None) -> str:
    """'ios' or 'android' when the request comes from one of the apps, else ''.

    The iOS app and the Android app send X-Drishti-Shell. A plain browser does not.
    """
    if request is None:
        return ""
    raw = (request.headers.get("x-drishti-shell") or "").strip().lower()
    return raw if raw in APP_SHELLS else ""


def allowed_platforms() -> tuple[str, ...] | None:
    """ASTROLOGER_CONNECT_PLATFORMS, e.g. "ios,android". None when unset: every client.

    "web" may be listed to keep the feature on in a plain browser as well.
    """
    raw = (os.environ.get("ASTROLOGER_CONNECT_PLATFORMS") or "").strip().lower()
    if not raw:
        return None
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def platform_allowed(request: Request | None) -> bool:
    allowed = allowed_platforms()
    if allowed is None:
        return True
    return (app_shell(request) or "web") in allowed


def enabled_for(request: Request | None) -> bool:
    """The feature is on, the store is ready, and this client may see it."""
    return store_ready() and platform_allowed(request)


def google_pay_enabled(request: Request | None) -> bool:
    """Google Pay in the Stripe wallet sheet. Android app only, and only when the flag is on."""
    return enabled_for(request) and app_shell(request) == "android" and _flag("ANDROID_GOOGLE_PAY_ENABLED")


def chat_locked_after_end(request: Request | None) -> bool:
    """Google Play's 1:1 service exception needs the session not to be available afterwards.

    On by default for the Android app. CONNECT_LOCK_ENDED_CHATS=1 locks every client.
    """
    return app_shell(request) == "android" or _flag("CONNECT_LOCK_ENDED_CHATS")


def _platform_gate(request: Request) -> None:
    path = request.url.path
    if not path.startswith("/api/connect/"):
        return
    if any(path.startswith(prefix) for prefix in _PLATFORM_EXEMPT):
        return
    if feature_on() and not platform_allowed(request):
        raise HTTPException(404, "connect_disabled")


router = APIRouter(dependencies=[Depends(_platform_gate)])


def public_plans() -> dict:
    if not store_ready():
        return {}
    import connect_money as money
    low, high = money.rate_bounds()
    return {
        "provider_trial_days": money.trial_days(),
        "provider_monthly_label": (os.environ.get("PROVIDER_MONTHLY_LABEL") or "$9.99").strip() or "$9.99",
        "provider_yearly_label": (os.environ.get("PROVIDER_YEARLY_LABEL") or "$99").strip() or "$99",
        "rate_min_cents": low,
        "rate_max_cents": high,
        "platform_commission_rate": (os.environ.get("PLATFORM_COMMISSION_RATE") or "0.05").strip() or "0.05",
    }


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


def _astro_id(request: Request, *, allow_password_change: bool = False) -> str:
    header = request.headers.get("authorization") or ""
    bearer = header[7:].strip() if header.lower().startswith("bearer ") else ""
    try:
        astro_id = store.astro_from_token(bearer)
    except store.ConnectError as e:
        raise HTTPException(e.status, e.code) from e
    if not allow_password_change and store.password_must_change(astro_id):
        raise HTTPException(409, "must_change")
    return astro_id


def _ios_shell(request: Request) -> bool:
    """True when the iOS app marks the request. Safari does not send this."""
    return app_shell(request) == "ios"


def _refuse_native_plan(request: Request) -> None:
    """The listing plan is sold on the website only, never inside the iOS or Android app."""
    if app_shell(request):
        raise HTTPException(403, "plan_not_in_app")


def _refuse_android_signup(request: Request) -> None:
    """Astrologers register on the website. The Android app offers sign-in only."""
    if app_shell(request) == "android":
        raise HTTPException(403, "signup_not_in_app")


def _run(fn):
    try:
        return fn()
    except store.ConnectError as e:
        detail = {"error": e.code, **e.extra} if e.extra else e.code
        raise HTTPException(e.status, detail) from e


def _stripe_error(err: connect_stripe.StripeError, *, payout: bool = False) -> HTTPException:
    code = connect_stripe.payout_code(err) if payout else (err.code or "stripe_rejected")
    return HTTPException(502, code)


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str
    name: str
    specialties: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    years: int = 0
    bio: str = ""
    rate_cents: int
    photo: str = ""


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str


class PasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
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


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str
    payment_intent: str


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
    action: Literal["approve", "suspend", "remove"]


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval: Literal["month", "year"]


class DemoCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str
    code: str = ""


@router.get("/api/connect/astrologers")
def api_list(device: str):
    _gate()
    return {
        "astrologers": _run(lambda: store.list_astrologers(device)),
        "demo_code": store.demo_code_configured(),
    }


@router.post("/api/connect/demo/unlock")
def api_demo_unlock(body: DemoCodeIn):
    _gate()
    return _run(lambda: store.unlock_demo(body.device, body.code))


@router.post("/api/connect/demo/credit")
def api_demo_credit(body: DeviceIn):
    _gate()
    return _run(lambda: store.grant_demo_credit(body.device))


@router.post("/api/connect/apply")
def api_apply(body: ApplyIn, request: Request):
    _gate()
    _refuse_android_signup(request)
    return _run(lambda: store.apply_astrologer(body.email, body.password, body.name, body.specialties, body.languages, body.years, body.bio, body.rate_cents, body.photo))


@router.post("/api/connect/astro/login")
def api_login(body: LoginIn):
    _gate()
    return _run(lambda: store.astro_login(body.email, body.password))


@router.post("/api/connect/astro/password")
def api_password(body: PasswordIn, request: Request):
    _gate()
    astro_id = _astro_id(request, allow_password_change=True)
    return _run(lambda: store.change_astro_password(astro_id, body.password))


@router.get("/api/connect/astro/me")
def api_me(request: Request):
    _gate()
    astro_id = _astro_id(request)
    return _run(lambda: store.astro_profile(astro_id))


class RateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_cents: int


@router.post("/api/connect/astro/rate")
def api_rate(body: RateIn, request: Request):
    _gate()
    astro_id = _astro_id(request)
    return _run(lambda: store.set_rate(astro_id, body.rate_cents))


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


@router.get("/api/connect/astro/live")
def api_astro_live(request: Request):
    _gate()
    astro_id = _astro_id(request)
    return _run(lambda: store.astro_live(astro_id))


def _payouts_ready(secret: str, account_id: str) -> bool:
    if not secret or not account_id:
        return False
    try:
        account = connect_stripe.retrieve_account(secret, account_id)
    except connect_stripe.StripeError:
        return False
    return connect_stripe.payouts_ready(account)


@router.get("/api/connect/astro/earnings")
def api_earnings(request: Request):
    _gate()
    astro_id = _astro_id(request)
    view = _run(lambda: store.earnings(astro_id))
    info = _run(lambda: store.astrologer_private(astro_id))
    account = info.get("stripe_account") or ""
    method = info.get("payout_method") or ""
    paypal_email = info.get("paypal_email") or ""
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    view["payout_method"] = method
    view["payout_mask"] = store.mask_email(paypal_email) if method == "paypal" else ""
    if method == "paypal" and paypal_email:
        view["has_payout_account"] = True
        view["payouts_ready"] = True
    else:
        view["has_payout_account"] = bool(account)
        view["payouts_ready"] = _payouts_ready(secret, account) if account else False
        if account and not method:
            view["payout_method"] = "stripe"
    return view


class PayoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["paypal", "stripe"]
    paypal_email: str = ""
    confirm_email: str = ""


@router.post("/api/connect/astro/payout")
def api_payout_method(body: PayoutIn, request: Request):
    _gate()
    astro_id = _astro_id(request)
    return _run(lambda: store.set_payout_method(astro_id, body.method, body.paypal_email, body.confirm_email))


@router.post("/api/connect/astro/stripe")
def api_stripe_onboard(request: Request):
    _gate()
    astro_id = _astro_id(request)
    _run(lambda: store.protect_demo(astro_id))
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    public = (os.environ.get("APP_PUBLIC_URL") or "").strip().rstrip("/")
    if not secret or not public:
        raise HTTPException(503, "stripe_not_configured")
    info = _run(lambda: store.astrologer_private(astro_id))
    account = info["stripe_account"]
    if not account:
        try:
            created = connect_stripe.create_express_account(secret, info["email"], "acct-astro-v2-" + astro_id)
        except connect_stripe.StripeError as e:
            raise _stripe_error(e, payout=True) from e
        account = str((created or {}).get("id") or "")
        if not account:
            raise HTTPException(502, "payout_setup_failed")
        store.set_stripe_account(astro_id, account)
    try:
        link = connect_stripe.create_account_link(secret, account, public + "/#astro", public + "/#astro")
    except connect_stripe.StripeError as e:
        raise _stripe_error(e, payout=True) from e
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
    try:
        intent = connect_stripe.create_payment_intent(secret, cents, {
            "device": body.device,
            "product_id": body.product_id,
            "purpose": "wallet",
        }, "pi-" + nonce)
    except connect_stripe.StripeError as e:
        raise _stripe_error(e) from e
    public = (os.environ.get("APP_PUBLIC_URL") or "").strip().rstrip("/")
    return {
        "id": intent.get("id") or "",
        "client_secret": intent.get("client_secret"),
        "product_id": body.product_id,
        "amount_cents": cents,
        "return_url": (public + "/#wallet") if public else "",
    }


@router.post("/api/connect/wallet/confirm")
def api_confirm(body: ConfirmIn):
    _gate()
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not secret:
        raise HTTPException(503, "stripe_not_configured")
    try:
        intent = connect_stripe.retrieve_payment_intent(secret, body.payment_intent)
    except connect_stripe.StripeError as e:
        raise HTTPException(502, e.code) from e
    status = str(intent.get("status") or "")
    meta = intent.get("metadata") or {}
    if status != "succeeded":
        if status in ("processing", "requires_action", "requires_confirmation", "requires_capture"):
            raise HTTPException(409, "payment_pending")
        raise HTTPException(400, "payment_invalid")
    device = (body.device or "").strip()
    if meta.get("purpose") != "wallet" or str(meta.get("device") or "") != device:
        raise HTTPException(400, "payment_invalid")
    result = _run(lambda: store.credit_topup(device, "stripe", str(meta.get("product_id") or ""), str(intent.get("id") or "")))
    return {"ok": True, **result}


@router.post("/api/connect/wallet/apple")
def api_apple(body: AppleIn):
    _gate()
    raise HTTPException(400, "live_sessions_use_stripe")


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
    kind = event.get("type") or ""
    if kind.startswith("customer.subscription.") or kind == "invoice.payment_failed":
        return _apply_subscription_event(event)
    if kind != "payment_intent.succeeded":
        return {"ok": True, "ignored": True}
    obj = (event.get("data") or {}).get("object") or {}
    meta = obj.get("metadata") or {}
    if meta.get("purpose") != "wallet":
        return {"ok": True, "ignored": True}
    payment_id = str(obj.get("id") or "")
    result = store.credit_topup(str(meta.get("device") or ""), "stripe", str(meta.get("product_id") or ""), payment_id)
    return {"ok": True, **result}


def _unix_iso(value) -> str | None:
    if not value:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _apply_subscription_event(event: dict) -> dict:
    kind = event.get("type") or ""
    obj = (event.get("data") or {}).get("object") or {}
    if kind == "invoice.payment_failed":
        customer = str(obj.get("customer") or "")
        sub = str(obj.get("subscription") or "")
        astro = ""
        meta = obj.get("metadata") or {}
        if isinstance(meta, dict):
            astro = str(meta.get("astrologer_id") or "")
        if not astro and customer:
            with __import__("db").connect() as con:
                store.ensure(con)
                row = con.execute("select id from ac_astrologer where stripe_customer=?", (customer,)).fetchone()
                astro = row[0] if row else ""
        if not astro:
            return {"ok": True, "ignored": True}
        return {"ok": True, **store.apply_provider_plan(astro, "past_due", None, None, customer, sub, "")}
    meta = (obj.get("metadata") or {}) if isinstance(obj, dict) else {}
    astro = str(meta.get("astrologer_id") or "")
    customer = str(obj.get("customer") or "")
    if not astro and customer:
        with __import__("db").connect() as con:
            store.ensure(con)
            row = con.execute("select id from ac_astrologer where stripe_customer=?", (customer,)).fetchone()
            astro = row[0] if row else ""
    if not astro:
        return {"ok": True, "ignored": True}
    items = ((obj.get("items") or {}).get("data") or [])
    interval = ""
    if items:
        interval = str((((items[0].get("price") or {}).get("recurring") or {}).get("interval")) or "")
    status = "canceled" if kind == "customer.subscription.deleted" else str(obj.get("status") or "")
    result = store.apply_provider_plan(
        astro, status, _unix_iso(obj.get("current_period_end")), _unix_iso(obj.get("trial_end")),
        customer, str(obj.get("id") or ""), interval)
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
    return _run(lambda: store.post_message(session_id, body.sender, body.body, body.device, astro_id))


@router.get("/api/connect/sessions/{session_id}/messages")
def api_messages(session_id: str, request: Request, device: str = "", after: int = 0):
    _gate()
    astro_id = None
    if request and request.headers.get("authorization"):
        astro_id = _astro_id(request)
    locked = chat_locked_after_end(request)
    return {"messages": _run(lambda: store.list_messages(session_id, after, device, astro_id, lock_after_end=locked))}


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


@router.post("/api/connect/sessions/{session_id}/demo-voice")
def api_demo_voice(session_id: str, body: DeviceIn):
    _gate()
    return _run(lambda: store.issue_demo_voice(
        session_id, body.device,
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


class DeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: str


@router.post("/api/connect/account/delete")
def api_delete_device(body: DeleteIn):
    _gate()
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()

    def refund(payment_id, cents, already):
        if not secret:
            raise connect_stripe.StripeError("stripe_not_configured")
        return connect_stripe.refund_payment(secret, payment_id, cents, f"refund-{payment_id}-{already}-{cents}")

    return _run(lambda: store.delete_device(body.device, refund))


@router.post("/api/connect/astro/delete")
def api_delete_astro(request: Request):
    _gate()
    astro_id = _astro_id(request)
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()

    def cancel(subscription_id):
        if not secret:
            raise connect_stripe.StripeError("stripe_not_configured")
        return connect_stripe.cancel_subscription(secret, subscription_id)

    return _run(lambda: store.delete_astrologer(astro_id, cancel))


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
    return _run(lambda: store.admin_decide(astrologer_id, body.action))


@router.post("/api/connect/admin/astrologers/{astrologer_id}/reset-password")
def api_admin_reset(astrologer_id: str, request: Request):
    _admin(request)
    return _run(lambda: store.reset_astro_password(astrologer_id))


@router.post("/api/connect/astro/trial")
def api_trial(request: Request):
    _gate()
    _refuse_native_plan(request)
    astro_id = _astro_id(request)
    return _run(lambda: store.grant_trial(astro_id))


@router.post("/api/connect/astro/plan")
def api_plan(body: PlanIn, request: Request):
    _gate()
    _refuse_native_plan(request)
    astro_id = _astro_id(request)
    _run(lambda: store.protect_demo(astro_id))
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    price_key = "STRIPE_PRICE_PROVIDER_MONTHLY" if body.interval == "month" else "STRIPE_PRICE_PROVIDER_YEARLY"
    price = (os.environ.get(price_key) or "").strip()
    public = (os.environ.get("APP_PUBLIC_URL") or "").strip().rstrip("/")
    if not secret or not price or not public:
        raise HTTPException(503, "stripe_not_configured")
    info = _run(lambda: store.astrologer_private(astro_id))
    customer = info["stripe_customer"]
    if not customer:
        try:
            created = connect_stripe.create_customer(secret, info["email"], astro_id)
        except connect_stripe.StripeError as e:
            raise _stripe_error(e) from e
        customer = str((created or {}).get("id") or "")
        if not customer:
            raise HTTPException(502, "stripe_rejected")
        store.set_stripe_customer(astro_id, customer)
    trial = 0 if info["trial_used"] else __import__("connect_money").trial_days()
    try:
        session = connect_stripe.create_checkout(secret, price, customer, public + "/#astro", public + "/#astro", astro_id, trial)
    except connect_stripe.StripeError as e:
        raise _stripe_error(e) from e
    return {"url": session.get("url") or ""}


@router.post("/api/connect/astro/portal")
def api_portal(request: Request):
    _gate()
    _refuse_native_plan(request)
    astro_id = _astro_id(request)
    _run(lambda: store.protect_demo(astro_id))
    secret = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    public = (os.environ.get("APP_PUBLIC_URL") or "").strip().rstrip("/")
    info = _run(lambda: store.astrologer_private(astro_id))
    if not info["stripe_customer"]:
        raise HTTPException(409, "no_billing_account")
    if not secret or not public:
        raise HTTPException(503, "stripe_not_configured")
    try:
        session = connect_stripe.create_portal(secret, info["stripe_customer"], public + "/#astro")
    except connect_stripe.StripeError as e:
        raise _stripe_error(e) from e
    return {"url": session.get("url") or ""}


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
    paypal_on = connect_paypal.configured()
    if not stripe_key and not paypal_on:
        return {"ok": True, "skipped": "payouts_not_configured"}
    sent = []
    for row in store.payout_candidates():
        if row.get("method") == "paypal":
            if not paypal_on:
                continue
            batch_key = store.payout_batch_id(row["astrologer_id"])
            try:
                paid = connect_paypal.send_payout(row["paypal_email"], row["amount_cents"], batch_key)
                external = paid.get("batch_id") or ""
            except connect_paypal.PayPalError as err:
                if not err.duplicate:
                    continue
                external = batch_key
            if not external:
                continue
            marked = store.mark_payout(row["astrologer_id"], row["amount_cents"], external, provider="paypal")
        else:
            if not stripe_key or not row.get("stripe_account"):
                continue
            if not _payouts_ready(stripe_key, row["stripe_account"]):
                continue
            try:
                transfer = connect_stripe.create_transfer(stripe_key, row["amount_cents"], row["stripe_account"], "transfer-" + row["astrologer_id"])
            except connect_stripe.StripeError:
                continue
            marked = store.mark_payout(row["astrologer_id"], row["amount_cents"], transfer.get("id") or "")
        sent.append(marked)
    return {"ok": True, "payouts": sent}
