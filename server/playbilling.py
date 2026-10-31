"""Verify a Google Play consumable before an Acharya question is answered.

The Play Console product ``acharya_question`` is a one-time consumable priced
at $0.99. This module calls the Android Publisher API. It does not trust the
price, product id, or purchase token sent by the phone.

The service-account JSON is read from the environment. It must never be
committed.

``PLAY_SERVICE_ACCOUNT_JSON``
    The key file contents (raw JSON, or base64 of that JSON).
``PLAY_SERVICE_ACCOUNT_FILE``
    Path to the key file, for a machine you control. Ignored when the JSON
    env var is set.
``PLAY_PACKAGE_NAME``
    Default ``com.ikshana.drishti``.
``PLAY_PRODUCT_ID``
    Default ``acharya_question``.
"""
from __future__ import annotations

import base64
import json
import os
from urllib.parse import quote

import httpx

SCOPE = "https://www.googleapis.com/auth/androidpublisher"
PUBLISHER = "https://androidpublisher.googleapis.com/androidpublisher/v3"
TOKEN_URI = "https://oauth2.googleapis.com/token"

_cache: dict = {"key": None, "client": None}


class PlayError(Exception):
    """A purchase cannot pay for a question. ``code`` is sent to the app."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def clear_client_cache() -> None:
    _cache["key"] = None
    _cache["client"] = None


def package_name() -> str:
    return os.environ.get("PLAY_PACKAGE_NAME", "com.ikshana.drishti").strip() or "com.ikshana.drishti"


def product_id() -> str:
    return os.environ.get("PLAY_PRODUCT_ID", "acharya_question").strip() or "acharya_question"


def _parse_account(raw: str) -> dict:
    text = raw.strip()
    if not text:
        raise PlayError("billing_not_configured", "Play Billing is not set up on the server.")
    try:
        if text.startswith("{"):
            info = json.loads(text)
        else:
            info = json.loads(base64.b64decode(text, validate=True).decode("utf-8"))
    except Exception as e:
        raise PlayError("billing_not_configured", "Play Billing is not set up on the server.") from e
    if not isinstance(info, dict) or "client_email" not in info or "private_key" not in info:
        raise PlayError("billing_not_configured", "Play Billing is not set up on the server.")
    info.setdefault("token_uri", TOKEN_URI)
    return info


def _load_account(raw: str, path: str) -> dict | None:
    if raw.strip():
        return _parse_account(raw)
    if not path.strip():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return _parse_account(f.read())
    except PlayError:
        raise
    except OSError as e:
        raise PlayError("billing_not_configured", "Play Billing is not set up on the server.") from e


def client_from_env() -> "PlayClient | None":
    """The live Play client, or None when no service account is configured.

    Bad JSON raises PlayError so a broken key cannot be treated as 'allow'.
    """
    raw = os.environ.get("PLAY_SERVICE_ACCOUNT_JSON", "")
    path = os.environ.get("PLAY_SERVICE_ACCOUNT_FILE", "")
    package = package_name()
    product = product_id()
    key = (raw, path, package, product)
    if _cache["key"] == key:
        return _cache["client"]
    info = _load_account(raw, path)
    client = None if info is None else PlayClient(package, product, info)
    _cache["key"] = key
    _cache["client"] = client
    return client


def _as_int(value, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise PlayError("payment_invalid", "Google Play did not accept this purchase.")


def validate_purchase(payload: dict, expected_product: str) -> dict:
    """Accept a real (or license-test) purchase of exactly one question.

    Promo codes and rewarded products are rejected: they would be a free question.
    """
    if not isinstance(payload, dict):
        raise PlayError("payment_invalid", "Google Play did not accept this purchase.")
    state = _as_int(payload.get("purchaseState"), default=-1)
    if state == 2:
        raise PlayError(
            "payment_pending",
            "Google Play is still processing this payment. The question was not answered. Try again in a moment.")
    if state != 0:
        raise PlayError("payment_invalid", "Google Play did not accept this purchase. The question was not answered.")
    purchase_type = payload.get("purchaseType")
    if purchase_type in ("", None):
        purchase_type = None
    else:
        purchase_type = _as_int(purchase_type, default=-1)
    # Absent = paid with money. 0 = license tester. 1 = promo. 2 = rewarded.
    if purchase_type not in (None, 0):
        raise PlayError(
            "payment_invalid",
            "A promo or rewarded product cannot pay for a question. The question was not answered.")
    quantity = _as_int(payload.get("quantity"), default=1)
    if quantity != 1:
        raise PlayError(
            "payment_invalid",
            "Each purchase pays for one question. Bundles are not sold.")
    order_id = str(payload.get("orderId") or "").strip()
    if not order_id or len(order_id) > 128:
        raise PlayError("payment_invalid", "Google Play did not accept this purchase. The question was not answered.")
    got = payload.get("productId")
    if got and str(got) != expected_product:
        raise PlayError("payment_invalid", "This purchase is not an Acharya question.")
    account = payload.get("obfuscatedExternalAccountId") or None
    if account is not None:
        account = str(account)
    return {
        "order_id": order_id,
        "purchase_type": purchase_type,
        "consumption_state": _as_int(payload.get("consumptionState"), default=0) or 0,
        "obfuscated_account": account,
    }


def _error_reason(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return ""
    errors = err.get("errors") or []
    if errors and isinstance(errors[0], dict) and errors[0].get("reason"):
        return str(errors[0]["reason"])
    return str(err.get("status") or "")


class PlayClient:
    """Android Publisher ``purchases.products`` get + consume."""

    def __init__(self, package: str, product: str, account_info: dict, http: httpx.Client | None = None):
        self.package_name = package
        self.product_id = product
        self._account = account_info
        self._http = http or httpx.Client(timeout=15.0)
        self._creds = None

    def _headers(self) -> dict:
        if self._creds is None or not self._creds.valid:
            from google.oauth2 import service_account
            import google.auth.transport.requests
            self._creds = service_account.Credentials.from_service_account_info(
                self._account, scopes=[SCOPE])
            self._creds.refresh(google.auth.transport.requests.Request())
        return {"Authorization": f"Bearer {self._creds.token}"}

    def _url(self, token: str) -> str:
        pkg = quote(self.package_name, safe="")
        sku = quote(self.product_id, safe="")
        tok = quote(token, safe="")
        return f"{PUBLISHER}/applications/{pkg}/purchases/products/{sku}/tokens/{tok}"

    def fetch(self, token: str) -> dict:
        try:
            response = self._http.get(self._url(token), headers=self._headers())
        except httpx.HTTPError as e:
            raise PlayError(
                "billing_unavailable",
                "Google Play could not be reached. The question was not answered. Try again with the same purchase.") from e
        return self._read_purchase(response)

    def _read_purchase(self, response: httpx.Response) -> dict:
        if response.status_code == 200:
            data = response.json()
            if not isinstance(data, dict):
                raise PlayError("payment_invalid", "Google Play did not accept this purchase.")
            return data
        if response.status_code in (401, 403):
            raise PlayError(
                "billing_not_configured",
                "The server is not allowed to check Play purchases yet. The question was not answered, and this purchase was not used.")
        if response.status_code >= 500:
            raise PlayError(
                "billing_unavailable",
                "Google Play could not be reached. The question was not answered. Try again with the same purchase.")
        raise PlayError("payment_invalid", "Google Play did not accept this purchase. The question was not answered.")

    def consume(self, token: str) -> None:
        """Mark the consumable used so the same person can buy another question.

        Already-consumed tokens are success: the entitlement was already granted.
        """
        try:
            response = self._http.post(self._url(token) + ":consume", headers=self._headers())
        except httpx.HTTPError as e:
            raise PlayError(
                "billing_unavailable",
                "Google Play could not be reached to finish the purchase.") from e
        if response.status_code in (200, 204):
            return
        reason = _error_reason(response)
        if response.status_code in (400, 409) and reason in ("productNotOwnedByUser", "alreadyConsumed", "purchaseTokenNoLongerValid"):
            return
        if response.status_code in (401, 403):
            raise PlayError("billing_not_configured", "The server is not allowed to finish Play purchases yet.")
        raise PlayError("billing_unavailable", "Google Play could not finish the purchase. It will be retried.")
