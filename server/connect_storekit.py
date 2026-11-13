"""App Store Server API checks for StoreKit 2 consumables.

The signed transaction is verified by asking Apple, not by trusting the phone.
Tests replace ``fetch_transaction``.
"""
from __future__ import annotations

import os
import time

import httpx


class StoreKitError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(code)
        self.code = code
        self.message = message or code


def configured() -> bool:
    needed = ("APPLE_ISSUER_ID", "APPLE_KEY_ID", "APPLE_IAP_PRIVATE_KEY", "APPLE_BUNDLE_ID")
    return all((os.environ.get(name) or "").strip() for name in needed)


def _bearer() -> str:
    """ES256 JWT for the App Store Server API. Requires PyJWT and cryptography."""
    try:
        import jwt
    except ImportError as e:
        raise StoreKitError("billing_not_configured", "App Store verification is not installed on the server.") from e
    now = int(time.time())
    token = jwt.encode(
        {"iss": os.environ["APPLE_ISSUER_ID"].strip(), "iat": now, "exp": now + 300, "aud": "appstoreconnect-v1", "bid": os.environ["APPLE_BUNDLE_ID"].strip()},
        os.environ["APPLE_IAP_PRIVATE_KEY"].replace("\\n", "\n"),
        algorithm="ES256",
        headers={"kid": os.environ["APPLE_KEY_ID"].strip(), "typ": "JWT"},
    )
    return token if isinstance(token, str) else token.decode()


def fetch_transaction(transaction_id: str) -> dict:
    """Return Apple's transaction fields. Override this in tests."""
    if not configured():
        raise StoreKitError("billing_not_configured", "App Store verification is not set up on the server.")
    host = "https://api.storekit-sandbox.itunes.apple.com" if (os.environ.get("APPLE_IAP_ENV") or "").strip() == "sandbox" else "https://api.storekit.itunes.apple.com"
    try:
        res = httpx.get(
            f"{host}/inApps/v1/transactions/{transaction_id}",
            headers={"Authorization": "Bearer " + _bearer()},
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise StoreKitError("billing_unavailable", "The App Store could not be reached.") from e
    if res.status_code == 404:
        raise StoreKitError("payment_invalid", "The App Store did not accept that purchase.")
    if res.status_code >= 400:
        raise StoreKitError("billing_unavailable", "The App Store could not be reached.")
    signed = (res.json() or {}).get("signedTransactionInfo") or ""
    payload = _jws_payload(signed)
    if (payload.get("bundleId") or "") != os.environ["APPLE_BUNDLE_ID"].strip():
        raise StoreKitError("payment_invalid", "The App Store did not accept that purchase.")
    if payload.get("revocationDate"):
        raise StoreKitError("payment_invalid", "That App Store purchase was revoked.")
    return {
        "transaction_id": str(payload.get("transactionId") or transaction_id),
        "product_id": str(payload.get("productId") or ""),
        "bundle_id": str(payload.get("bundleId") or ""),
    }


def _jws_payload(token: str) -> dict:
    import base64
    import json
    try:
        body = token.split(".")[1]
    except IndexError as e:
        raise StoreKitError("payment_invalid", "The App Store did not accept that purchase.") from e
    pad = "=" * (-len(body) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(body + pad))
    except (json.JSONDecodeError, ValueError) as e:
        raise StoreKitError("payment_invalid", "The App Store did not accept that purchase.") from e


def verify_transaction(transaction_id: str, signed_transaction: str | None = None) -> dict:
    """Prefer the server-to-Apple lookup. The phone's JWS is not trusted on its own."""
    info = fetch_transaction((transaction_id or "").strip())
    if signed_transaction:
        claimed = _jws_payload(signed_transaction)
        if str(claimed.get("transactionId") or "") not in ("", info["transaction_id"]):
            raise StoreKitError("payment_invalid", "The App Store did not accept that purchase.")
        if claimed.get("productId") and claimed.get("productId") != info["product_id"]:
            raise StoreKitError("payment_invalid", "The App Store did not accept that purchase.")
    return info
