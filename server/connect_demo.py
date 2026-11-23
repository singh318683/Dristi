"""App Review demo. Hidden unless this server is in review mode or the device entered the code.

Credentials come from the environment. This module never logs them.
"""
from __future__ import annotations

import hashlib
import hmac
import os

DEMO_ID = "demo-review"
DEMO_NAME = "Demo Astrologer (App Review)"
VOICE_IDENTITY = "demo-voice"
GRANT_CENTS = 500
GRANT_CAP_CENTS = 2000
REPLY_EVERY_SECONDS = 6
LINES = (
    "Hello, I'm the demo astrologer for App Review. This is a scripted reply, not a live person.",
    "Ask a sample question. I'll keep replying with this short demo script.",
    "A voice call plays a short greeting so you can confirm the audio. These sessions are not recorded.",
    "End the session whenever you like. The charge uses review credit only, not a card.",
)


def review_all() -> bool:
    """Every device on this server can see the demo. Leave unset on production and on Android."""
    return (os.environ.get("DEMO_REVIEW") or "").strip() == "1"


def code_configured() -> bool:
    return bool((os.environ.get("DEMO_REVIEW_CODE") or "").strip())


def code_matches(offered: str) -> bool:
    expect = (os.environ.get("DEMO_REVIEW_CODE") or "").strip()
    offered = (offered or "").strip()
    if not expect or not offered:
        return False
    return hmac.compare_digest(
        hashlib.sha256(expect.encode()).digest(),
        hashlib.sha256(offered.encode()).digest(),
    )


def credentials() -> tuple[str, str]:
    """Email and password from the environment. Empty when either is missing or too short."""
    email = (os.environ.get("DEMO_ASTRO_EMAIL") or "").strip().lower()
    password = (os.environ.get("DEMO_ASTRO_PASSWORD") or "").strip()
    if "@" not in email or len(password) < 8:
        return "", ""
    return email, password


def configured() -> bool:
    email, password = credentials()
    return bool(email and password)
