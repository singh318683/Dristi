"""Stripe PaymentIntents, Connect Express, and transfer helpers. No Stripe SDK."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time

import httpx

log = logging.getLogger("drishti.stripe")
_SECRET = re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]+|whsec_[A-Za-z0-9]+")
# Express Dashboard plus Stripe as the losses collector needs this preview version.
ACCOUNT_API_VERSION = "2026-10-28.preview"


class StripeError(Exception):
    def __init__(self, code: str, stripe_code: str = "", message: str = "", status: int = 0):
        super().__init__(code)
        self.code = code
        self.stripe_code = stripe_code or ""
        self.stripe_message = message or ""
        self.status = int(status or 0)


def _safe(text: str) -> str:
    return _SECRET.sub("[redacted]", str(text or ""))[:400]


def payout_code(err: StripeError) -> str:
    blob = f"{getattr(err, 'stripe_message', '')} {getattr(err, 'stripe_code', '')}".lower()
    if "signed up for connect" in blob:
        return "connect_not_enabled"
    if "not enabled" in blob and ("platform" in blob or "connect" in blob):
        return "connect_not_enabled"
    return "payout_setup_failed"


def _reject(res) -> None:
    stripe_code = ""
    message = ""
    try:
        body = res.json()
    except (ValueError, TypeError):
        body = None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        stripe_code = _safe(err.get("code") or err.get("type") or "")[:80]
        message = _safe(err.get("message") or "")
    log.warning("stripe error status=%s code=%s message=%s", getattr(res, "status_code", 0), stripe_code, message)
    raise StripeError("stripe_rejected", stripe_code=stripe_code, message=message, status=getattr(res, "status_code", 0) or 0)


def verify_event(payload: bytes, header: str, secret: str, now: int | None = None, tolerance: int = 300) -> dict:
    now = int(time.time() if now is None else now)
    ts = None
    sigs: list[str] = []
    for item in (header or "").split(","):
        key, _, value = item.strip().partition("=")
        if key == "t" and value.isdigit():
            ts = int(value)
        elif key == "v1" and value:
            sigs.append(value)
    if ts is None or not sigs or abs(now - ts) > tolerance:
        raise StripeError("invalid_signature")
    signed = str(ts).encode() + b"." + payload
    expect = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expect, sig) for sig in sigs):
        raise StripeError("invalid_signature")
    return json.loads(payload)


def _form(secret: str, path: str, data: dict, idempotency_key: str | None = None) -> dict:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    try:
        res = httpx.post(
            "https://api.stripe.com/v1/" + path,
            data=data,
            auth=(secret, ""),
            headers=headers,
            timeout=20,
        )
    except httpx.HTTPError as e:
        log.warning("stripe unreachable error=%s", type(e).__name__)
        raise StripeError("stripe_unavailable") from e
    if res.status_code >= 400:
        _reject(res)
    return res.json()


def retrieve_payment_intent(secret: str, intent_id: str) -> dict:
    intent_id = (intent_id or "").strip()
    if not intent_id.startswith("pi_") or not intent_id.replace("_", "").isalnum():
        raise StripeError("bad_payment")
    try:
        res = httpx.get(
            "https://api.stripe.com/v1/payment_intents/" + intent_id,
            auth=(secret, ""),
            timeout=20,
        )
    except httpx.HTTPError as e:
        log.warning("stripe unreachable error=%s", type(e).__name__)
        raise StripeError("stripe_unavailable") from e
    if res.status_code >= 400:
        _reject(res)
    return res.json()


def create_payment_intent(secret: str, amount_cents: int, metadata: dict, idempotency_key: str) -> dict:
    flat = {
        "amount": str(int(amount_cents)),
        "currency": "usd",
        "payment_method_types[]": "card",
    }
    for key, value in metadata.items():
        flat[f"metadata[{key}]"] = str(value)
    return _form(secret, "payment_intents", flat, idempotency_key)


def connected_account_body(email: str) -> dict:
    """Accounts v2. Stripe collects losses; the platform pays Stripe fees.

    A recipient-only account cannot set losses_collector to stripe, so the
    merchant card_payments capability is requested as well. Transfers still
    arrive through stripe_transfers. Country stays US.
    """
    return {
        "contact_email": email,
        "dashboard": "express",
        "identity": {"country": "US"},
        "defaults": {
            "responsibilities": {
                "fees_collector": "application",
                "losses_collector": "stripe",
            },
        },
        "configuration": {
            "merchant": {"capabilities": {"card_payments": {"requested": True}}},
            "recipient": {
                "capabilities": {
                    "stripe_balance": {"stripe_transfers": {"requested": True}},
                },
            },
        },
        "include": ["configuration.merchant", "configuration.recipient", "identity", "defaults"],
    }


def account_link_body(account_id: str, refresh_url: str, return_url: str) -> dict:
    return {
        "account": account_id,
        "use_case": {
            "type": "account_onboarding",
            "account_onboarding": {
                "configurations": ["merchant", "recipient"],
                "refresh_url": refresh_url,
                "return_url": return_url,
            },
        },
    }


def _json(secret: str, path: str, payload: dict, idempotency_key: str | None = None) -> dict:
    headers = {"Stripe-Version": ACCOUNT_API_VERSION}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        res = httpx.post(
            "https://api.stripe.com/" + path.lstrip("/"),
            json=payload,
            auth=(secret, ""),
            headers=headers,
            timeout=20,
        )
    except httpx.HTTPError as e:
        log.warning("stripe unreachable error=%s", type(e).__name__)
        raise StripeError("stripe_unavailable") from e
    if res.status_code >= 400:
        _reject(res)
    return res.json()


def create_express_account(secret: str, email: str, idempotency_key: str) -> dict:
    return _json(secret, "v2/core/accounts", connected_account_body(email), idempotency_key)


def create_account_link(secret: str, account_id: str, refresh_url: str, return_url: str) -> dict:
    return _json(secret, "v2/core/account_links", account_link_body(account_id, refresh_url, return_url))


def create_customer(secret: str, email: str, astrologer_id: str) -> dict:
    return _form(secret, "customers", {
        "email": email,
        "metadata[astrologer_id]": astrologer_id,
    }, "cus-" + astrologer_id)


def create_checkout(secret: str, price: str, customer: str, success: str, cancel: str, astrologer_id: str, trial_days: int) -> dict:
    data = {
        "mode": "subscription",
        "customer": customer,
        "success_url": success,
        "cancel_url": cancel,
        "client_reference_id": astrologer_id,
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        "metadata[astrologer_id]": astrologer_id,
        "subscription_data[metadata][astrologer_id]": astrologer_id,
    }
    if trial_days > 0:
        data["subscription_data[trial_period_days]"] = str(int(trial_days))
    return _form(secret, "checkout/sessions", data, None)


def create_portal(secret: str, customer: str, return_url: str) -> dict:
    return _form(secret, "billing_portal/sessions", {"customer": customer, "return_url": return_url}, None)


def create_transfer(secret: str, amount_cents: int, destination: str, idempotency_key: str) -> dict:
    # v2 accounts with stripe_transfers still receive these v1 transfers.
    return _form(secret, "transfers", {
        "amount": str(int(amount_cents)),
        "currency": "usd",
        "destination": destination,
    }, idempotency_key)

