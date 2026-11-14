"""Integer-cent pricing for astrologer sessions. The client never supplies these numbers."""
from __future__ import annotations

import os

MIN_MINUTES = 5
WALLET_PRODUCTS = {
    "wallet_5": 500,
    "wallet_10": 1000,
    "wallet_20": 2000,
}
ACHARYA_PRODUCT = "acharya_question"
ACHARYA_CENTS = 99


class MoneyError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _bps(raw: str) -> int:
    """'0.05' -> 500. A whole number is already basis points only when it is at least 100; otherwise it is a percent."""
    text = (raw or "").strip()
    if not text or text.count(".") > 1 or text.startswith("-"):
        raise MoneyError("bad_commission")
    whole, dot, frac = text.partition(".")
    if whole and not whole.isdigit():
        raise MoneyError("bad_commission")
    if frac and not frac.isdigit():
        raise MoneyError("bad_commission")
    if dot:
        bps = int(whole or "0") * 10000 + int((frac + "0000")[:4])
    else:
        bps = int(whole or "0") * 100
    if bps < 0 or bps >= 10000:
        raise MoneyError("bad_commission")
    return bps


def _money_cents(raw: str | None, default: str) -> int:
    """'0.30' or '5.00' are dollars. A whole number is cents, so 30 and 500 match those defaults."""
    text = (raw or "").strip() or default
    if text.count(".") > 1 or text.startswith("-"):
        raise MoneyError("bad_rate")
    whole, dot, frac = text.partition(".")
    if (whole and not whole.isdigit()) or (frac and not frac.isdigit()):
        raise MoneyError("bad_rate")
    if dot:
        return int(whole or "0") * 100 + int((frac + "00")[:2])
    return int(whole or "0")


def platform_bps() -> int:
    """Drishti's share of a live session. Default 5%."""
    return _bps((os.environ.get("PLATFORM_COMMISSION_RATE") or "0.05").strip() or "0.05")


def rate_bounds() -> tuple[int, int]:
    low = _money_cents(os.environ.get("ASTRO_RATE_MIN"), "0.30")
    high = _money_cents(os.environ.get("ASTRO_RATE_MAX"), "5.00")
    if low <= 0 or high < low:
        raise MoneyError("bad_rate")
    return low, high


def check_rate(cents: int) -> int:
    cents = int(cents)
    low, high = rate_bounds()
    if cents < low or cents > high:
        raise MoneyError("bad_rate")
    return cents


def trial_days() -> int:
    raw = (os.environ.get("PROVIDER_TRIAL_DAYS") or "90").strip() or "90"
    if not raw.isdigit():
        raise MoneyError("bad_trial")
    return max(0, min(365, int(raw)))


def grace_days() -> int:
    raw = (os.environ.get("PROVIDER_GRACE_DAYS") or "3").strip() or "3"
    if not raw.isdigit():
        raise MoneyError("bad_trial")
    return max(0, min(30, int(raw)))


def product_cents(product_id: str) -> int:
    cents = WALLET_PRODUCTS.get((product_id or "").strip())
    if not cents:
        raise MoneyError("unknown_product")
    return cents


def hold_cents(rate_cents: int) -> int:
    return check_rate(rate_cents) * MIN_MINUTES


def draw_buckets(stripe_cents: int, apple_cents: int, need: int) -> tuple[int, int]:
    """Spend Stripe balance first, then App Store balance. Raises if the sum is short."""
    need = int(need)
    stripe_cents = int(stripe_cents)
    apple_cents = int(apple_cents)
    if need < 0 or stripe_cents < 0 or apple_cents < 0:
        raise MoneyError("bad_amount")
    if stripe_cents + apple_cents < need:
        raise MoneyError("insufficient_balance")
    take_stripe = min(stripe_cents, need)
    return take_stripe, need - take_stripe


def elapsed_cost(rate_cents: int, elapsed_seconds: int) -> int:
    """Pro-rata per minute, floored to the cent. Zero before the clock starts."""
    rate = int(rate_cents)
    seconds = int(elapsed_seconds)
    if rate <= 0 or seconds <= 0:
        return 0
    return (seconds * rate) // 60


def paid_seconds(hold_total: int, rate_cents: int) -> int:
    rate = int(rate_cents)
    if rate <= 0:
        return 0
    return (int(hold_total) * 60) // rate


def split_gross(gross_cents: int) -> tuple[int, int]:
    """Provider keeps the rest of the gross. Drishti's share defaults to 5%.

    The two numbers sum to the gross. Live sessions are paid with Stripe, including
    Apple Pay, so this is not reduced by an App Store commission.
    """
    gross = int(gross_cents)
    if gross < 0:
        raise MoneyError("bad_amount")
    platform = gross * platform_bps() // 10000
    return gross - platform, platform


def money_label(cents: int, per_minute: bool = False) -> str:
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    body = f"${cents // 100}.{cents % 100:02d}"
    return sign + body + ("/min" if per_minute else "")
