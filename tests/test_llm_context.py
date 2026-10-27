"""Chart context sent to the chat model. Locks the 1990-05-15 Delhi timeline the model was misreading."""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import engine  # noqa: E402
import llm  # noqa: E402

# Delhi from server/data/cities.tsv. 10:30 IST = 05:00 UTC.
DELHI = (28.65195, 77.23149)
NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
PROFILE = {"name": "Asha", "gender": "female", "date": "1990-05-15", "time": "10:30",
           "place": "Delhi, India"}


def _utc():
    utc, info = engine.local_to_utc("1990-05-15", "10:30", "Asia/Kolkata", DELHI[1])
    assert info["offset_hours"] == 5.5 and utc == datetime(1990, 5, 15, 5, 0, tzinfo=timezone.utc)
    return utc


def _chart(now=NOW, time_known=True, time="10:30"):
    utc, _ = engine.local_to_utc("1990-05-15", time, "Asia/Kolkata", DELHI[1])
    return engine.compute_chart(utc, DELHI[0], DELHI[1], time_known=time_known, now=now)


CHART = _chart()
CTX = llm.compact_chart(PROFILE, CHART)
CITE = CTX["vimshottari"]["cite_only"]


def _closed(rows, start, end, first_lord):
    assert rows, "empty period list"
    assert rows[0]["start"] == start and rows[-1]["end"] == end
    assert rows[0]["lord"] == first_lord
    i = engine.DASHA_ORDER.index(first_lord)
    expect = [engine.DASHA_ORDER[(i + k) % 9] for k in range(len(rows))]
    assert [r["lord"] for r in rows] == expect
    for a, b in zip(rows, rows[1:]):
        assert a["end"] == b["start"]
        assert a["end"] > a["start"]


def test_reported_chart_dasha_dates_and_levels():
    """Rahu/Sun is current; Jupiter in Dec 2026 is a pratyantardasha, not an antardasha."""
    active = CITE["active"]
    assert CITE["as_of"] == "2026-10-27"
    assert active["mahadasha"] == {
        "level": "mahadasha", "lord": "Rahu", "start": "2011-12-09", "end": "2029-12-09"}
    assert active["antardasha"]["lord"] == "Sun"
    assert active["antardasha"]["start"] == "2026-06-28"
    assert active["antardasha"]["end"] == "2027-05-23"
    assert active["antardasha"]["mahadasha"] == "Rahu"
    pd = active["pratyantardasha"]
    assert pd["lord"] == "Jupiter" and pd["start"] == "2026-10-18" and pd["end"] == "2026-12-01"
    assert pd["antardasha"] == "Sun" and pd["mahadasha"] == "Rahu"

    # The bad answer treated "Jupiter until December 2026" as if it were on the antardasha timeline.
    assert CITE["antardashas"]["inside"] == "Rahu mahadasha"
    assert CITE["pratyantardashas"]["inside"] == "Sun antardasha, Rahu mahadasha"
    covering = [a for a in CITE["antardashas"]["rows"] if a["start"] <= "2026-12-01" < a["end"]]
    assert [a["lord"] for a in covering] == ["Sun"]
    jupiter_ads = [a for a in CITE["antardashas"]["rows"] if a["lord"] == "Jupiter"]
    assert jupiter_ads and jupiter_ads[0]["end"] < "2020-01-01"

    moon = next(a for a in CITE["antardashas"]["rows"] if a["lord"] == "Moon" and a["start"] > "2026")
    assert moon["start"] == "2027-05-23" and moon["end"] == "2028-11-20"
    assert "Moon antardasha" not in CITE["now_line"]
    assert CITE["now_line"] == (
        "Rahu mahadasha (2011-12-09 to 2029-12-09) > "
        "Sun antardasha (2026-06-28 to 2027-05-23) > "
        "Jupiter pratyantardasha (2026-10-18 to 2026-12-01)")
    assert "Jupiter pratyantardasha (2026-10-18 to 2026-12-01)" in CITE["do_not_collapse"]
    assert "inside Sun antardasha (until 2027-05-23)" in CITE["do_not_collapse"]
    assert "does not end the antardasha" in CITE["do_not_collapse"]
    assert "as the antardasha or the mahadasha" in CITE["do_not_collapse"]
    assert "still inside Sun antardasha" in CITE["next_line"]
    assert "Moon 2027-05-23 to 2028-11-20" in CITE["next_line"]
    # Mock LLM and the old client path still find the active mahadasha here.
    assert CTX["vimshottari"]["current"]["mahadasha"]["lord"] == "Rahu"
    assert CTX["vimshottari"]["current"]["pratyantardasha"]["antardasha"] == "Sun"


def test_same_lord_pratyantar_is_not_denied_as_the_antardasha():
    """First pratyantar shares the antardasha's lord. The note must not say that antardasha does not exist."""
    c = _chart(now=datetime(2026, 7, 1, tzinfo=timezone.utc))
    cite = llm.compact_chart(PROFILE, c)["vimshottari"]["cite_only"]
    assert cite["active"]["antardasha"]["lord"] == "Sun"
    assert cite["active"]["pratyantardasha"]["lord"] == "Sun"
    assert "not a Sun antardasha" not in cite["do_not_collapse"]
    assert "inside Sun antardasha" in cite["do_not_collapse"]
    assert "does not end the antardasha" in cite["do_not_collapse"]


def test_cite_only_sequences_match_vimshottari_order():
    active = CITE["active"]
    as_of = CITE["as_of"]
    for key in ("mahadasha", "antardasha", "pratyantardasha"):
        p = active[key]
        assert p["start"] <= as_of < p["end"]
    assert active["mahadasha"]["start"] <= active["antardasha"]["start"]
    assert active["antardasha"]["end"] <= active["mahadasha"]["end"]
    assert active["antardasha"]["start"] <= active["pratyantardasha"]["start"]
    assert active["pratyantardasha"]["end"] <= active["antardasha"]["end"]
    _closed(CITE["antardashas"]["rows"], active["mahadasha"]["start"], active["mahadasha"]["end"], "Rahu")
    _closed(CITE["pratyantardashas"]["rows"], active["antardasha"]["start"], active["antardasha"]["end"], "Sun")
    mds = CITE["mahadashas"]
    for a, b in zip(mds, mds[1:]):
        assert a["end"] == b["start"]
    assert [m["lord"] for m in mds if m.get("now")] == ["Rahu"]
    assert [a["lord"] for a in CITE["antardashas"]["rows"] if a.get("now")] == ["Sun"]
    assert [p["lord"] for p in CITE["pratyantardashas"]["rows"] if p.get("now")] == ["Jupiter"]
    nxt = CTX["vimshottari"]["current"]["next_antardasha"]
    assert CITE["next_antardasha_pratyantardashas"]["inside"] == "Moon antardasha, Rahu mahadasha"
    _closed(CITE["next_antardasha_pratyantardashas"]["rows"], nxt["start"], nxt["end"], nxt["lord"])


def test_dasha_lengths_follow_the_120_year_rule():
    """Independent of the row builder: Sun AD inside Rahu, Jupiter PD inside that AD."""
    moon = CHART["planets"]["Moon"]["longitude"]
    frac = (moon % engine.NAK_SPAN) / engine.NAK_SPAN
    assert engine.DASHA_ORDER[int(moon // engine.NAK_SPAN) % 9] == "Sun"
    balance_days = (1 - frac) * engine.DASHA_YEARS["Sun"] * engine.YEAR_DAYS
    expect_end = (_utc() + timedelta(days=balance_days)).date()
    sun = next(m for m in CITE["mahadashas"] if m["lord"] == "Sun" and m["start"] < "1990-05-15" < m["end"])
    assert abs((expect_end - date.fromisoformat(sun["end"])).days) <= 1

    ad = CITE["active"]["antardasha"]
    pd = CITE["active"]["pratyantardasha"]
    ad_days = (date.fromisoformat(ad["end"]) - date.fromisoformat(ad["start"])).days
    assert abs(ad_days - 18 * 6 / 120 * engine.YEAR_DAYS) <= 2
    pd_days = (date.fromisoformat(pd["end"]) - date.fromisoformat(pd["start"])).days
    assert abs(pd_days - (18 * 6 / 120) * (16 / 120) * engine.YEAR_DAYS) <= 2


def test_every_cited_period_exists_in_the_engine():
    known = set()
    for m in CHART["dasha"]["mahadashas"]:
        known.add((m["lord"], m["start"], m["end"]))
        for a in m["antardashas"]:
            known.add((a["lord"], a["start"], a["end"]))
    cur = CHART["dasha"]["current"]
    for p in cur["pratyantardashas"] + cur["next_antardasha_pratyantardashas"]:
        known.add((p["lord"], p["start"], p["end"]))
    rows = (CITE["mahadashas"] + CITE["antardashas"]["rows"] + CITE["pratyantardashas"]["rows"]
            + CITE["next_antardasha_pratyantardashas"]["rows"] + list(CITE["active"].values()))
    for r in rows:
        assert (r["lord"], r["start"], r["end"]) in known


def test_next_antardasha_can_fall_in_the_next_mahadasha():
    c = _chart(now=datetime(2029, 6, 1, tzinfo=timezone.utc))
    ctx = llm.compact_chart(PROFILE, c)
    cite = ctx["vimshottari"]["cite_only"]
    assert cite["active"]["mahadasha"]["lord"] == "Rahu"
    assert cite["active"]["antardasha"]["lord"] == "Mars"
    assert cite["active"]["antardasha"]["end"] == cite["active"]["mahadasha"]["end"]
    nxt = ctx["vimshottari"]["current"]["next_antardasha"]
    assert nxt["lord"] == "Jupiter" and nxt["mahadasha"] == "Jupiter"
    assert nxt["start"] == "2029-12-09"
    block = cite["next_antardasha_pratyantardashas"]
    assert block["inside"] == "Jupiter antardasha, Jupiter mahadasha"
    _closed(block["rows"], nxt["start"], nxt["end"], "Jupiter")


def test_no_period_outside_the_computed_span():
    c = _chart(now=datetime(2200, 1, 1, tzinfo=timezone.utc))
    ctx = llm.compact_chart(PROFILE, c)
    assert ctx["vimshottari"]["current"] == {}
    cite = ctx["vimshottari"]["cite_only"]
    assert cite["active"] is None and cite["mahadashas"]
    assert "Do not invent" in cite["rule"]


def test_lordships_aspects_and_divisional_signs():
    assert CTX["lagna"]["sign"] == "Cancer" and CTX["lagna"]["reliable"] is True
    assert CTX["planets"]["Mars"]["rules_houses"] == [5, 10]
    assert CTX["planets"]["Saturn"]["rules_houses"] == [7, 8]
    assert CTX["planets"]["Rahu"]["rules_houses"] == [] and CTX["planets"]["Ketu"]["rules_houses"] == []
    for h in CTX["houses"]:
        assert h["house"] in CTX["planets"][h["lord"]]["rules_houses"]
        assert h["lord_dignity"] == CTX["planets"][h["lord"]]["dignity"]
        assert h["lord_in_sign"] == CTX["planets"][h["lord"]]["sign"]
    h10 = CTX["houses"][9]
    assert h10["house"] == 10 and h10["lord"] == "Mars" and h10["occupants"] == ["Mercury"]
    for name, natal in CHART["planets"].items():
        row = CTX["planets"][name]
        assert row["d9"] == natal["navamsa_sign"]
        assert row["d10"] == natal["dashamsha_sign"]
        assert row["dignity"] == natal["dignity"]
        assert row["combust"] == natal["combust"] and row["retrograde"] == natal["retrograde"]
        assert row["aspects_houses"] == natal["aspects_houses"]
    assert CTX["divisional_lagnas"]["reliable"] is True
    assert CTX["divisional_lagnas"]["d9_lagna"] == CHART["lagna"]["navamsa_sign"]
    assert CTX["divisional_lagnas"]["d10_lagna"] == CHART["lagna"]["dashamsha_sign"]
    assert "navamsa_d9" not in CTX  # per-planet d9/d10 replaces the redundant sign map


def test_gochar_is_from_lagna_and_moon_and_ingresses_are_real():
    for name, v in CHART["transits"]["planets"].items():
        row = CTX["gochar"]["planets"][name]
        assert row["sign"] == v["sign"]
        assert row["from_lagna"] == v["house_from_lagna"]
        assert row["from_moon"] == v["house_from_moon"]
    raw = []
    for planet in ("Jupiter", "Saturn", "Rahu"):
        for dt, sign in engine.sign_changes(planet, NOW, 2.0, step_days=4):
            raw.append((dt, planet, engine.SIGNS[sign]))
    raw.sort(key=lambda r: (r[0].date().isoformat(), r[1]))
    events = CTX["gochar"]["ingresses"]["events"]
    assert events == [{"planet": p, "enters": s, "date": dt.date().isoformat()} for dt, p, s in raw]
    assert any(e["planet"] == "Jupiter" and e["enters"] == "Leo" for e in events)
    for dt, planet, sign in raw:
        before = engine.sidereal_positions(engine.jd_ut(dt - timedelta(minutes=1)))[planet][0]
        after = engine.sidereal_positions(engine.jd_ut(dt + timedelta(minutes=1)))[planet][0]
        assert engine.SIGNS[int(after // 30) % 12] == sign
        assert int(before // 30) % 12 != int(after // 30) % 12


def test_unknown_birth_time_does_not_offer_a_lagna():
    c = _chart(time_known=False, time=None)
    assert c["planets"]["Moon"]["house"] == 1 and c["lagna"]["reliable"] is False
    profile = dict(PROFILE, time=None)
    ctx = llm.compact_chart(profile, c)
    assert ctx["settings"]["time_known"] is False
    assert ctx["settings"]["house_reference"].startswith("Moon")
    assert ctx["lagna"] == {"reliable": False, "note": "Do not cite a rising sign. Birth time is unknown."}
    assert ctx["divisional_lagnas"]["reliable"] is False
    assert "d9_lagna" not in ctx["divisional_lagnas"] and "d10_lagna" not in ctx["divisional_lagnas"]
    assert "time_warning" in ctx and "Chandra lagna" in ctx["time_warning"]
    saturn = ctx["gochar"]["planets"]["Saturn"]
    assert "from_lagna" not in saturn and "from_moon" in saturn
    assert "from_moon only" in ctx["gochar"]["note"]
    # Planet divisional signs do not need a birth time.
    assert ctx["planets"]["Venus"]["d9"] == c["planets"]["Venus"]["navamsa_sign"]
    assert ctx["planets"]["Venus"]["d10"] == c["planets"]["Venus"]["dashamsha_sign"]
    assert ctx["vimshottari"]["cite_only"]["active"]["mahadasha"]["level"] == "mahadasha"


def test_prompt_rules_and_payload_stay_compact():
    text = llm.SYSTEM_PROMPT.format(app="Drishti", lang_rule=llm.LANG_RULES["en"])
    hindi = llm.SYSTEM_PROMPT.format(app="Drishti", lang_rule=llm.LANG_RULES["hi"])
    llm.HOROSCOPE_PROMPT.format(app="Drishti", sign="Mesha", lang_rule=llm.LANG_RULES["en"])
    for phrase in ("cite_only", "do_not_collapse", "pratyantardasha", "from_lagna", "from_moon",
                   "rules_houses", "D10", "doctor", "8th house", "time_known", "YYYY-MM-DD"):
        assert phrase in text
    assert "प्रत्यंतर" in hindi and "YYYY-MM-DD" in hindi
    assert "sub-period" in llm.LANG_RULES["hi"]
    blob = json.dumps(CTX, ensure_ascii=False, separators=(",", ":"))
    assert len(blob) < 12500
    assert len(llm.SYSTEM_PROMPT) < 5000
    # The old ambiguous keys are gone, so there is one timeline.
    assert "mahadasha_timeline" not in blob and "antardashas_in_current_mahadasha" not in blob
    assert "transits_now" not in blob


@pytest.mark.parametrize("planet,sign,deg,expect", [
    ("Mercury", 5, 25, "own sign"),
    ("Mercury", 5, 10, "exalted"),
    ("Venus", 11, 18, "exalted"),
])
def test_compact_dignity_matches_engine(planet, sign, deg, expect):
    assert engine.dignity(planet, sign, deg) == expect
