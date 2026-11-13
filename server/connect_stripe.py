"""Stripe PaymentIntents, Connect Express, and transfer helpers. No Stripe SDK."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx


class StripeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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
        raise StripeError("stripe_unavailable") from e
    if res.status_code >= 400:
        raise StripeError("stripe_rejected")
    return res.json()


def create_payment_intent(secret: str, amount_cents: int, metadata: dict, idempotency_key: str) -> dict:
    flat = {
        "amount": str(int(amount_cents)),
        "currency": "usd",
        "automatic_payment_methods[enabled]": "true",
    }
    for key, value in metadata.items():
        flat[f"metadata[{key}]"] = str(value)
    return _form(secret, "payment_intents", flat, idempotency_key)


def create_express_account(secret: str, email: str, idempotency_key: str) -> dict:
    return _form(secret, "accounts", {
        "type": "express",
        "country": "US",
        "email": email,
        "capabilities[transfers][requested]": "true",
    }, idempotency_key)


def create_account_link(secret: str, account_id: str, refresh_url: str, return_url: str) -> dict:
    return _form(secret, "account_links", {
        "account": account_id,
        "refresh_url": refresh_url,
        "return_url": return_url,
        "type": "account_onboarding",
    })


def create_transfer(secret: str, amount_cents: int, destination: str, idempotency_key: str) -> dict:
    return _form(secret, "transfers", {
        "amount": str(int(amount_cents)),
        "currency": "usd",
        "destination": destination,
    }, idempotency_key)

