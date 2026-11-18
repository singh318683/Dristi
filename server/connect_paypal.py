"""PayPal payouts. Client credentials stay in the environment and are never logged."""
from __future__ import annotations

import os

import httpx


class PayPalError(Exception):
    def __init__(self, code: str, duplicate: bool = False):
        super().__init__(code)
        self.code = code
        self.duplicate = duplicate


def _env() -> str:
    return (os.environ.get("PAYPAL_ENV") or "").strip().lower()


def base_url() -> str:
    if _env() == "live":
        return "https://api-m.paypal.com"
    if _env() == "sandbox":
        return "https://api-m.sandbox.paypal.com"
    return ""


def configured() -> bool:
    client = (os.environ.get("PAYPAL_CLIENT_ID") or "").strip()
    secret = (os.environ.get("PAYPAL_CLIENT_SECRET") or "").strip()
    return bool(base_url() and client and secret)


def _token() -> str:
    client = (os.environ.get("PAYPAL_CLIENT_ID") or "").strip()
    secret = (os.environ.get("PAYPAL_CLIENT_SECRET") or "").strip()
    try:
        res = httpx.post(
            base_url() + "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(client, secret),
            headers={"Accept": "application/json"},
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise PayPalError("paypal_unavailable") from e
    if res.status_code >= 400:
        raise PayPalError("paypal_rejected")
    token = (res.json() or {}).get("access_token") or ""
    if not token:
        raise PayPalError("paypal_rejected")
    return token


def _duplicate(body: dict) -> bool:
    details = body.get("details") if isinstance(body, dict) else None
    if not isinstance(details, list):
        return False
    for item in details:
        if not isinstance(item, dict):
            continue
        issue = str(item.get("issue") or "").lower()
        field = str(item.get("field") or "").lower()
        if "already exists" in issue or field == "sender_batch_id":
            return True
    return False


def send_payout(email: str, amount_cents: int, sender_batch_id: str) -> dict:
    if not configured():
        raise PayPalError("paypal_not_configured")
    cents = int(amount_cents)
    value = f"{cents // 100}.{cents % 100:02d}"
    payload = {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": "Drishti weekly earnings",
            "email_message": "Drishti weekly earnings",
        },
        "items": [{
            "recipient_type": "EMAIL",
            "amount": {"value": value, "currency": "USD"},
            "receiver": email,
            "note": "Drishti weekly earnings",
            "sender_item_id": sender_batch_id,
        }],
    }
    try:
        res = httpx.post(
            base_url() + "/v1/payments/payouts",
            json=payload,
            headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json"},
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise PayPalError("paypal_unavailable") from e
    body = {}
    try:
        body = res.json() or {}
    except ValueError:
        body = {}
    if res.status_code >= 400:
        raise PayPalError("paypal_duplicate" if _duplicate(body) else "paypal_rejected", duplicate=_duplicate(body))
    header = body.get("batch_header") if isinstance(body, dict) else None
    batch_id = ""
    if isinstance(header, dict):
        batch_id = str(header.get("payout_batch_id") or "")
    if not batch_id:
        raise PayPalError("paypal_rejected")
    return {"batch_id": batch_id, "status": str(header.get("batch_status") or "")}
