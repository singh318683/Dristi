"""Verse of the day from a curated Bhagavad Gita list.

Sanskrit in data/gita.json was checked, verse by verse, against the IIT Kanpur
moolam (heritage.iitk.ac.in) and the public shloka text. The English and Hindi
lines are original short summaries, not a modern copyrighted translation.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date
from functools import lru_cache

EPOCH = date(2020, 1, 1)
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "gita.json")


@lru_cache(maxsize=1)
def verses() -> tuple[dict, ...]:
    with open(_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return tuple(data)


def step_for(n: int) -> int:
    """Largest jump at or below n/3 that shares no factor with n.

    Consecutive days land in different parts of the list, and a verse does not
    come around again until every verse has been shown.
    """
    for step in range(max(1, n // 3), 0, -1):
        if math.gcd(step, n) == 1:
            return step
    return 1


def index_for(day: date, n: int | None = None) -> int:
    n = len(verses()) if n is None else n
    return ((day - EPOCH).days * step_for(n)) % n


def verse_for(day: date) -> dict:
    rows = verses()
    row = rows[index_for(day, len(rows))]
    return {
        "ref": row["ref"],
        "chapter": row["chapter"],
        "verse": row["verse"],
        "sa": row["sa"],
        "en": row["en"],
        "hi": row["hi"],
        "date": day.isoformat(),
    }
