"""Short-lived LiveKit join tokens. Media stays on LiveKit; this server only signs JWTs."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sign_jwt(payload: dict, secret: str) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(sig)}"


def read_jwt(token: str, secret: str) -> dict:
    try:
        header, body, sig = token.split(".")
    except ValueError as e:
        raise ValueError("bad_token") from e
    expect = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64(expect), sig):
        raise ValueError("bad_token")
    return json.loads(_unb64(body))


def join_token(api_key: str, api_secret: str, room: str, identity: str, ttl: int = 600, now: int | None = None) -> str:
    now = int(time.time() if now is None else now)
    ttl = max(60, min(int(ttl), 3600))
    return sign_jwt({
        "iss": api_key,
        "sub": identity,
        "nbf": now - 5,
        "exp": now + ttl,
        "video": {"roomJoin": True, "room": room, "canPublish": True, "canSubscribe": True},
    }, api_secret)


def webhook_token(secret: str, body: bytes, now: int | None = None) -> str:
    now = int(time.time() if now is None else now)
    return sign_jwt({
        "sha256": hashlib.sha256(body).hexdigest(),
        "nbf": now - 5,
        "exp": now + 300,
    }, secret)


def verify_webhook(authorization: str, body: bytes, secret: str, now: int | None = None) -> dict:
    token = (authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    claims = read_jwt(token, secret)
    now = int(time.time() if now is None else now)
    if int(claims.get("exp") or 0) < now or int(claims.get("nbf") or 0) > now + 5:
        raise ValueError("bad_token")
    digest = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(str(claims.get("sha256") or ""), digest):
        raise ValueError("bad_token")
    return claims
