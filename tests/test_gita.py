"""Gita verse of the day: the curated file, the daily rotation, and the open API."""
import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import app  # noqa: E402
import gita  # noqa: E402

DEVANAGARI = re.compile(r"[\u0900-\u097F]")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DRISHTI_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from fastapi.testclient import TestClient
    return TestClient(app.app)


def test_curated_file_is_valid():
    rows = gita.verses()
    assert len(rows) >= 61
    refs = []
    for row in rows:
        for key in ("ref", "chapter", "verse", "sa", "en", "hi"):
            assert key in row and str(row[key]).strip()
        assert row["ref"] == f"{row['chapter']}.{row['verse']}"
        assert isinstance(row["chapter"], int) and isinstance(row["verse"], int)
        assert DEVANAGARI.search(row["sa"])
        assert row["sa"].strip().endswith("॥")
        assert not re.search(r"[0-9]", row["sa"])
        assert "उवाच" not in row["sa"]
        assert row["en"].strip() != row["hi"].strip()
        refs.append(row["ref"])
    assert len(refs) == len(set(refs))
    raw = json.loads(open(gita._PATH, encoding="utf-8").read())
    assert [r["ref"] for r in raw] == refs


def test_rotation_is_stable_and_does_not_repeat_within_60_days():
    n = len(gita.verses())
    step = gita.step_for(n)
    assert math.gcd(step, n) == 1
    start = date(2026, 10, 27)
    first = gita.verse_for(start)
    assert first == gita.verse_for(start)
    assert first["date"] == "2026-10-27"
    assert first["ref"] == gita.verses()[gita.index_for(start)]["ref"]
    window = [gita.verse_for(start + timedelta(days=i))["ref"] for i in range(60)]
    assert len(window) == len(set(window))
    assert gita.verse_for(start)["ref"] != gita.verse_for(start + timedelta(days=60))["ref"]
    cycle = [gita.verse_for(start + timedelta(days=i))["ref"] for i in range(n)]
    assert len(set(cycle)) == n
    assert gita.verse_for(start)["ref"] == gita.verse_for(start + timedelta(days=n))["ref"]
    # A date before the epoch still lands on one verse, and the same date again matches.
    early = gita.verse_for(date(2019, 5, 1))
    assert early["ref"] in {row["ref"] for row in gita.verses()}
    assert early == gita.verse_for(date(2019, 5, 1))


def test_homepage_inlines_the_verse_list(client):
    html = client.get("/").text
    assert 'id="gita-splash"' in html
    assert "gita-card" not in html
    assert html.count('"sa":') >= 61
    assert '"ref":"2.47"' in html
    assert "window.__GITA_INJECTED__" not in html
    assert "Date.UTC(2020, 0, 1)" in html


def test_gita_api_needs_no_login_and_follows_the_date(client):
    pinned = client.get("/api/gita", params={"date": "2026-10-27"})
    assert pinned.status_code == 200
    body = pinned.json()
    expect = gita.verse_for(date(2026, 10, 27))
    assert body == expect
    assert "public" in pinned.headers["cache-control"]
    other = client.get("/api/gita", params={"date": "2026-10-28"}).json()
    assert other["ref"] != body["ref"]
    assert other["date"] == "2026-10-28"
    bad = client.get("/api/gita", params={"date": "27/10/2026"})
    assert bad.status_code == 400
    missing = client.get("/api/gita")
    assert missing.status_code == 200
    assert missing.json()["date"] == datetime.now(timezone.utc).date().isoformat()
    assert missing.json()["ref"] == gita.verse_for(datetime.now(timezone.utc).date())["ref"]
