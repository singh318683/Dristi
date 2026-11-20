"""App Store Server API checks for StoreKit 2 consumables.

The signed transaction is verified by asking Apple, not by trusting the phone.
Tests replace ``fetch_transaction``.
"""
from __future__ import annotations

import logging
import os
import re
import time

import httpx

log = logging.getLogger("drishti.storekit")


class StoreKitError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(code)
        self.code = code
        self.message = message or code


def configured() -> bool:
    needed = ("APPLE_ISSUER_ID", "APPLE_KEY_ID", "APPLE_IAP_PRIVATE_KEY", "APPLE_BUNDLE_ID")
    return all((os.environ.get(name) or "").strip() for name in needed)


def _wrap_b64(body: str) -> str:
    return "\n".join(body[i:i + 64] for i in range(0, len(body), 64))


def apple_private_key() -> str:
    """Turn the env value into a PEM. Does not log the key."""
    text = (os.environ.get("APPLE_IAP_PRIVATE_KEY") or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    header = re.search(r"-----BEGIN ([A-Z0-9 ]+)-----", text)
    if header:
        kind = header.group(1).strip()
        footer = f"-----END {kind}-----"
        start = header.end()
        end = text.find(footer)
        body = text[start:end] if end >= 0 else text[start:]
        b64 = "".join(body.split())
        if not b64:
            return ""
        return f"-----BEGIN {kind}-----\n{_wrap_b64(b64)}\n{footer}\n"
    b64 = "".join(text.split())
    if not b64:
        return ""
    return "-----BEGIN PRIVATE KEY-----\n" + _wrap_b64(b64) + "\n-----END PRIVATE KEY-----\n"


def key_parses() -> bool:
    pem = apple_private_key()
    if "-----BEGIN" not in pem:
        return False
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        load_pem_private_key(pem.encode(), password=None)
    except Exception as e:
        log.warning("apple key did not parse error=%s", type(e).__name__)
        return False
    return True


def _bearer() -> str:
    """ES256 JWT for the App Store Server API. Requires PyJWT and cryptography."""
    try:
        import jwt
    except ImportError as e:
        raise StoreKitError("billing_not_configured", "App Store verification is not installed on the server.") from e
    now = int(time.time())
    try:
        token = jwt.encode(
            {"iss": os.environ["APPLE_ISSUER_ID"].strip(), "iat": now, "exp": now + 300, "aud": "appstoreconnect-v1", "bid": os.environ["APPLE_BUNDLE_ID"].strip()},
            apple_private_key(),
            algorithm="ES256",
            headers={"kid": os.environ["APPLE_KEY_ID"].strip(), "typ": "JWT"},
        )
    except Exception as e:
        log.warning("apple jwt failed error=%s", type(e).__name__)
        raise StoreKitError("billing_key_invalid", "The App Store key on the server could not be read.") from e
    return token if isinstance(token, str) else token.decode()


def jwt_ok() -> bool:
    """True when a verification JWT can be signed. The token is not returned."""
    if not configured() or not key_parses():
        return False
    try:
        token = _bearer()
    except StoreKitError:
        return False
    return isinstance(token, str) and token.count(".") == 2


def apple_status() -> dict:
    """Key health only. Never includes the key, the JWT, or Apple's response."""
    try:
        ready = configured()
        parses = key_parses() if ready else False
        signed = jwt_ok() if parses else False
    except Exception as e:
        log.warning("apple status failed error=%s", type(e).__name__)
        return {"apple_configured": False, "apple_key_parses": False, "apple_jwt_ok": False}
    if not ready:
        kind = "missing"
    elif not parses:
        kind = "key"
    elif not signed:
        kind = "jwt"
    else:
        kind = "signed"
    return {
        "apple_configured": ready,
        "apple_key_parses": parses,
        "apple_jwt_ok": signed,
        "apple_key_kind": kind,
    }


PRODUCTION_HOST = "https://api.storekit.itunes.apple.com"
SANDBOX_HOST = "https://api.storekit-sandbox.itunes.apple.com"
# Apple's App Store Server API: invalid id, and transaction id not in that environment.
NOT_FOUND_CODES = {4000006, 4040010, 4040005, 4040006}
_attempts: list[dict] = []


def _hosts() -> list[str]:
    """Production, sandbox, or production then sandbox when Apple says the transaction is missing.

    TestFlight and App Review purchases live in the sandbox API. A production 404 is that case
    unless APPLE_IAP_ENV pins one host.
    """
    env = (os.environ.get("APPLE_IAP_ENV") or "").strip().lower()
    if env == "sandbox":
        return [SANDBOX_HOST]
    if env == "production":
        return [PRODUCTION_HOST]
    return [PRODUCTION_HOST, SANDBOX_HOST]


def _read_transaction(res, transaction_id: str) -> dict:
    try:
        body = res.json() or {}
    except Exception as e:
        log.warning("apple transaction body failed error=%s", type(e).__name__)
        raise StoreKitError("billing_unavailable", "The App Store could not be checked.") from e
    signed = body.get("signedTransactionInfo") or ""
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


def fetch_transaction(transaction_id: str) -> dict:
    """Return Apple's transaction fields. Override this in tests."""
    if not configured():
        raise StoreKitError("billing_not_configured", "App Store verification is not set up on the server.")
    try:
        return _fetch_transaction(transaction_id)
    except StoreKitError:
        raise
    except Exception as e:
        log.warning("apple fetch failed error=%s", type(e).__name__)
        raise StoreKitError("billing_unavailable", "The App Store could not be checked.") from e


def _host_label(host: str) -> str:
    return "sandbox" if "sandbox" in host else "production"


def _error_body(res) -> tuple[int | None, str]:
    """Apple's errorCode and a short errorMessage. Nothing else from the body."""
    try:
        body = res.json() or {}
    except Exception:
        return None, ""
    if not isinstance(body, dict):
        return None, ""
    raw = body.get("errorCode")
    code = None
    if isinstance(raw, int):
        code = raw
    elif isinstance(raw, str) and raw.isdigit():
        code = int(raw)
    message = body.get("errorMessage")
    if not isinstance(message, str):
        message = ""
    message = " ".join(message.split())[:180]
    if "BEGIN " in message or message.count(".") >= 2 and len(message) > 80:
        message = ""
    return code, message


def _kind(status: int, error_code: int | None) -> str:
    if status == 401 or (error_code is not None and 4010000 <= error_code < 4020000):
        return "auth"
    if status == 404 or error_code in NOT_FOUND_CODES:
        return "not_found"
    if status >= 400:
        return "unavailable"
    return "ok"


def _remember(host: str, status: int, error_code: int | None, error_message: str) -> dict:
    kind = _kind(status, error_code)
    label = _host_label(host)
    log.warning(
        "apple lookup host=%s status=%s errorCode=%s errorMessage=%s",
        label, status, "" if error_code is None else error_code, error_message,
    )
    attempt = {
        "host": label,
        "status": status,
        "errorCode": error_code,
        "errorMessage": error_message,
        "kind": kind,
    }
    _attempts.append(attempt)
    return attempt


def _fetch_transaction(transaction_id: str) -> dict:
    global _attempts
    _attempts = []
    bearer = _bearer()
    hosts = _hosts()
    for index, host in enumerate(hosts):
        try:
            res = httpx.get(
                f"{host}/inApps/v1/transactions/{transaction_id}",
                headers={"Authorization": "Bearer " + bearer},
                timeout=20,
            )
        except httpx.HTTPError as e:
            _remember(host, 0, None, "unreachable")
            raise StoreKitError("billing_unavailable", "The App Store could not be reached.") from e
        error_code, error_message = _error_body(res) if res.status_code >= 400 else (None, "")
        attempt = _remember(host, res.status_code, error_code, error_message)
        if attempt["kind"] in ("auth", "not_found") and index < len(hosts) - 1:
            continue
        if res.status_code < 400:
            return _read_transaction(res, transaction_id)
        if attempt["kind"] == "auth" or any(item["kind"] == "auth" for item in _attempts):
            raise StoreKitError(
                "billing_unauthorized",
                "The App Store rejected the server credentials. Check the key id, issuer id, and bundle id.",
            )
        if attempt["kind"] == "not_found":
            raise StoreKitError("payment_invalid", "The App Store did not accept that purchase.")
        raise StoreKitError("billing_unavailable", "The App Store could not be reached.")
    raise StoreKitError("payment_invalid", "The App Store did not accept that purchase.")


def apple_diagnostics() -> dict:
    """One live lookup of a dummy id, plus key health. No key material and no JWT."""
    status = apple_status()
    env = (os.environ.get("APPLE_IAP_ENV") or "").strip().lower() or "auto"
    status["apple_env"] = env
    status["attempts"] = []
    status["apple_last_status"] = None
    status["apple_last_error_code"] = None
    status["apple_last_error_message"] = ""
    status["apple_last_host"] = ""
    status["apple_last_kind"] = status["apple_key_kind"]
    if not status["apple_jwt_ok"]:
        return status
    try:
        fetch_transaction("0")
        status["apple_last_kind"] = "ok"
    except StoreKitError:
        pass
    status["attempts"] = list(_attempts)
    if _attempts:
        last = _attempts[-1]
        status["apple_last_status"] = last["status"]
        status["apple_last_error_code"] = last["errorCode"]
        status["apple_last_error_message"] = last["errorMessage"]
        status["apple_last_host"] = last["host"]
        status["apple_last_kind"] = last["kind"]
    return status


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
