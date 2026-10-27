"""Daily panchang: tithi/yoga/karana formulas, and a Delhi day from Swiss Ephemeris."""
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import engine  # noqa: E402
import panchang  # noqa: E402

DELHI = (28.65195, 77.23149)


def test_formula_edges():
    assert panchang.tithi_index(0) == 0
    assert panchang.tithi_index(11.9) == 0
    assert panchang.tithi_index(12) == 1
    assert panchang.tithi_index(180) == 15  # Purnima has ended; Krishna Pratipada
    assert panchang.tithi_index(359.9) == 29
    assert panchang.yoga_index(0, 0) == 0
    assert panchang.yoga_index(5, 5) == 0
    assert panchang.yoga_index(engine.NAK_SPAN, 0) == 1
    assert panchang.karana_index(0) == 0
    assert panchang.karana_names(0) == ("Kimstughna", "किंस्तुघ्न")
    assert panchang.karana_names(1)[0] == "Bava"
    assert panchang.karana_names(57)[0] == "Shakuni"
    assert panchang.karana_names(59)[0] == "Naga"
    assert len(panchang.TITHI_EN) == len(panchang.TITHI_HI) == 30
    assert len(panchang.YOGA_EN) == len(panchang.YOGA_HI) == 27
    assert len(panchang.NAK_HI) == 27
    # Mon 2, Tue 7, Wed 5, Thu 6, Fri 4, Sat 3, Sun 8
    assert panchang.RAHU_EIGHTH == (2, 7, 5, 6, 4, 3, 8)


def test_delhi_tuesday_rahu_is_seventh_eighth():
    p = panchang.panchang("2026-10-27", DELHI[0], DELHI[1], "Asia/Kolkata")
    assert p["date"] == "2026-10-27"
    assert p["vaar"]["en"] == "Tuesday" and p["vaar"]["hi"] == "मंगलवार"
    assert p["rahu_kaal"]["eighth"] == 7
    # Apparent sunrise for this ephemeris and refraction model.
    assert p["sunrise"] == "2026-10-27T06:29:27+05:30"
    assert p["sunset"] == "2026-10-27T17:40:07+05:30"
    rise = datetime.fromisoformat(p["sunrise"])
    sett = datetime.fromisoformat(p["sunset"])
    rk0 = datetime.fromisoformat(p["rahu_kaal"]["start"])
    rk1 = datetime.fromisoformat(p["rahu_kaal"]["end"])
    assert rise < sett
    assert abs((rk1 - rk0) - (sett - rise) / 8) < timedelta(seconds=2)
    assert rise <= rk0 < rk1 <= sett
    # 7th eighth starts after six parts of the day.
    assert abs((rk0 - rise) - (sett - rise) * 6 / 8) < timedelta(seconds=2)
    for key in ("tithi", "nakshatra", "yoga", "karana"):
        assert p[key]["en"] and p[key]["hi"] and p[key]["end"]
    # Nakshatra at sunrise matches the chart engine's sidereal Moon.
    jd = engine.jd_ut(rise)
    moon = engine.sidereal_positions(jd)["Moon"][0]
    assert engine.nak_info(moon)["nakshatra"] == p["nakshatra"]["en"]
    assert p["nakshatra"]["en"] == "Bharani"
    assert p["tithi"]["en"] == "Krishna Pratipada"


def test_rejects_bad_place():
    with pytest.raises(ValueError):
        panchang.panchang("2026-10-27", 91, 0, "UTC")
    with pytest.raises(Exception):
        panchang.panchang("2026-10-27", 28.6, 77.2, "Not/AZone")
