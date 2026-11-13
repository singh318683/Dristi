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


def commission_bps() -> int:
    """Apple's cut in basis points. 0.15 -> 1500. Default 15%."""
    raw = (os.environ.get("APPLE_COMMISSION_RATE") or "0.15").strip() or "0.15"
    if raw.count(".") > 1 or raw.startswith("-"):
        raise MoneyError("bad_commission")
    whole, dot, frac = raw.partition(".")
    if not whole and not frac:
        raise MoneyError("bad_commission")
    if whole and not whole.isdigit():
        raise MoneyError("bad_commission")
    if frac and not frac.isdigit():
        raise MoneyError("bad_commission")
    bps = int(whole or "0") * 10000 + int((frac + "0000")[:4])
    if bps < 0 or bps >= 10000:
        raise MoneyError("bad_commission")
    return bps


def product_cents(product_id: str) -> int:
    cents = WALLET_PRODUCTS.get((product_id or "").strip())
    if not cents:
        raise MoneyError("unknown_product")
    return cents


def hold_cents(rate_cents: int) -> int:
    rate = int(rate_cents)
    if rate < 50 or rate > 5000:
        raise MoneyError("bad_rate")
    return rate * MIN_MINUTES


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


def net_of_apple(stripe_part: int, apple_part: int, bps: int | None = None) -> tuple[int, int]:
    """Return (net cents that can be split, apple fee cents). Stripe funds are already net."""
    bps = commission_bps() if bps is None else int(bps)
    apple_part = int(apple_part)
    stripe_part = int(stripe_part)
    fee = apple_part * bps // 10000
    net = stripe_part + (apple_part - fee)
    return net, fee


def split_net(net_cents: int) -> tuple[int, int]:
    """Astrologer 75%, platform the remainder, so the two sides sum to net."""
    net = int(net_cents)
    if net < 0:
        raise MoneyError("bad_amount")
    astro = net * 75 // 100
    return astro, net - astro


def money_label(cents: int, per_minute: bool = False) -> str:
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    body = f"${cents // 100}.{cents % 100:02d}"
    return sign + body + ("/min" if per_minute else "")
