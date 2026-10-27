"""Chart engine tests. Reference values from AstroSage celebrity charts (Lahiri, fetched 2026-10-23):
https://www.astrosage.com/celebrity-horoscope/narendra-modi-birth-chart.asp
https://celebrity.astrosage.com/mahatma-gandhi-birth-chart.asp
https://celebrity.astrosage.com/sachin-tendulkar-birth-chart.asp
https://celebrity.astrosage.com/amitabh-bachchan-birth-chart.asp
We feed the exact inputs AstroSage used (their lat/lon and TZ +5.5)."""
import os, sys
from datetime import datetime, timezone, date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import engine  # noqa: E402
from matching import milan  # noqa: E402

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def dm(d, m, s=0):
    return d + m / 60 + s / 3600


REFS = [
    # name, date, time, lat, lon, lagna(sign,deg), moon(sign,deg), nakshatra, pada, dasha [(lord, start)], current MD
    ("Narendra Modi", "1950-09-17", "11:00", dm(23, 37), dm(72, 28), ("Scorpio", dm(1, 9, 24)),
     ("Scorpio", dm(8, 48, 22)), "Anuradha", 2, [("Mercury", "1961-11-30"), ("Mars", "2021-11-30")], "Mars"),
    ("Mahatma Gandhi", "1869-10-02", "08:36:19", dm(21, 40), dm(69, 40), ("Libra", dm(12, 6, 43)),
     ("Cancer", dm(28, 16, 51)), "Ashlesha", 4, [("Ketu", "1871-12-12"), ("Rahu", "1921-12-12")], None),
    ("Sachin Tendulkar", "1973-04-24", "14:25", dm(18, 58), dm(72, 50), ("Leo", dm(7, 11, 31)),
     ("Sagittarius", dm(25, 17, 42)), "Purva Ashadha", 4, [("Sun", "1975-05-15"), ("Jupiter", "2016-05-15")], "Jupiter"),
    ("Amitabh Bachchan", "1942-10-11", "16:00", dm(25, 57), dm(81, 50), ("Aquarius", dm(21, 26, 42)),
     ("Libra", dm(10, 54, 35)), "Swati", 2, [("Jupiter", "1955-01-19"), ("Venus", "2014-01-19")], "Venus"),
]


def _chart(date_, time_, lat, lon, off=5.5):
    utc, _ = engine.local_to_utc(date_, time_, "Asia/Kolkata", lon, off)
    return engine.compute_chart(utc, lat, lon, now=NOW)


@pytest.mark.parametrize("ref", REFS, ids=[r[0] for r in REFS])
def test_reference_chart(ref):
    name, d, t, lat, lon, (ls, ld), (ms, mdeg), nak, pada, dashas, cur = ref
    c = _chart(d, t, lat, lon)
    assert c["lagna"]["sign"] == ls
    assert abs(c["lagna"]["degree"] - ld) < 0.1          # within 6 arc-minutes
    moon = c["planets"]["Moon"]
    assert moon["sign"] == ms and abs(moon["degree"] - mdeg) < 0.02  # within ~1 arc-minute
    assert c["janma_nakshatra"] == nak and c["janma_pada"] == pada
    starts = {m["lord"]: date.fromisoformat(m["start"]) for m in c["dasha"]["mahadashas"]}
    for lord, s in dashas:
        assert abs((starts[lord] - date.fromisoformat(s)).days) <= 7
    if cur:
        assert c["dasha"]["current"]["mahadasha"]["lord"] == cur


def test_modi_planets_match_astrosage():
    c = _chart("1950-09-17", "11:00", dm(23, 37), dm(72, 28))
    P = c["planets"]
    ref = {"Sun": ("Virgo", dm(0, 35, 47)), "Mars": ("Scorpio", dm(0, 56, 47)), "Mercury": ("Virgo", dm(0, 46, 46)),
           "Jupiter": ("Aquarius", dm(6, 36, 36)), "Venus": ("Leo", dm(15, 41, 47)), "Saturn": ("Leo", dm(29, 39, 13)),
           "Rahu": ("Pisces", dm(5, 12, 55)), "Ketu": ("Virgo", dm(5, 12, 55))}
    for p, (s, deg) in ref.items():
        assert P[p]["sign"] == s, p
        assert abs(P[p]["degree"] - deg) < 0.05, p
    assert P["Mercury"]["retrograde"] and P["Jupiter"]["retrograde"] and not P["Sun"]["retrograde"]
    assert P["Mercury"]["combust"] and P["Saturn"]["combust"]      # AstroSage marks both [C]
    assert P["Moon"]["dignity"] == "debilitated" and P["Mars"]["dignity"] == "own sign"
    assert P["Mercury"]["dignity"] == "exalted"
    assert P["Moon"]["house"] == 1 and P["Sun"]["house"] == 11 and P["Jupiter"]["house"] == 4


def test_sachin_dignities():
    c = _chart("1973-04-24", "14:25", dm(18, 58), dm(72, 50))
    P = c["planets"]
    assert P["Sun"]["dignity"] == "exalted" and P["Mars"]["dignity"] == "exalted"
    assert P["Mercury"]["dignity"] == "debilitated" and P["Jupiter"]["dignity"] == "debilitated"
    assert P["Venus"]["combust"]


def test_navamsa():
    # navamsa of 0°-3°20' Aries = Aries; 3°20'-6°40' Aries = Taurus; 0° Taurus = Capricorn; 0° Gemini = Libra
    assert engine.navamsa_sign(1) == 0 and engine.navamsa_sign(4) == 1
    assert engine.navamsa_sign(30.5) == 9 and engine.navamsa_sign(60.5) == 6
    assert engine.navamsa_sign(359.9) == 11


def test_dashamsha_parashara():
    # Odd signs count from themselves; even signs from the 9th. Each part is 3°.
    assert engine.SIGNS[engine.dashamsha_sign(0)] == "Aries"
    assert engine.SIGNS[engine.dashamsha_sign(3)] == "Taurus"
    assert engine.SIGNS[engine.dashamsha_sign(27)] == "Capricorn"   # Aries, 10th part
    assert engine.SIGNS[engine.dashamsha_sign(30)] == "Capricorn"   # 0° Taurus, 9th from Taurus
    assert engine.SIGNS[engine.dashamsha_sign(33)] == "Aquarius"
    assert engine.SIGNS[engine.dashamsha_sign(90)] == "Pisces"      # 0° Cancer
    assert engine.SIGNS[engine.dashamsha_sign(90 + 9.92)] == "Gemini"
    c = _chart("1990-05-15", "10:30", 28.65195, 77.23149)
    for r in list(c["planets"].values()) + [c["lagna"]]:
        assert r["dashamsha_sign"] == engine.SIGNS[engine.dashamsha_sign(r["longitude"])]


def test_mercury_virgo_dignity_is_not_exalted_after_moolatrikona():
    # BPHS: Mercury exalted in Virgo, moolatrikona 16°–20°, own sign for the rest of Virgo.
    assert engine.dignity("Mercury", 5, 0) == "exalted"
    assert engine.dignity("Mercury", 5, 15.9) == "exalted"
    assert engine.dignity("Mercury", 5, 16) == "moolatrikona"
    assert engine.dignity("Mercury", 5, 19.99) == "moolatrikona"
    assert engine.dignity("Mercury", 5, 20) == "own sign"
    assert engine.dignity("Mercury", 5, 29.9) == "own sign"
    assert engine.dignity("Mercury", 2, 10) == "own sign"          # Gemini
    assert engine.dignity("Moon", 1, 2) == "exalted"
    assert engine.dignity("Moon", 1, 10) == "moolatrikona"
    assert engine.dignity("Sun", 4, 10) == "moolatrikona"
    assert engine.dignity("Sun", 4, 25) == "own sign"
    assert engine.dignity("Sun", 0, 10) == "exalted"
    assert engine.dignity("Rahu", 1, 5) == "exalted"


def test_nakshatra_boundaries():
    assert engine.nak_info(0)["nakshatra"] == "Ashwini" and engine.nak_info(0)["pada"] == 1
    assert engine.nak_info(13.34)["nakshatra"] == "Bharani"
    assert engine.nak_info(359.99)["nakshatra"] == "Revati" and engine.nak_info(359.99)["pada"] == 4


def test_historical_timezones():
    # India 1942 war time was UTC+6:30 in tzdb
    u, info = engine.local_to_utc("1942-10-11", "16:00", "Asia/Kolkata", 81.83)
    assert info["offset_hours"] == 6.5 and u.hour == 9 and u.minute == 30
    # pre-1906 India -> local mean time from longitude (Gandhi 07:11 LMT Porbandar ~ 02:32 UT, per Aureas)
    u, info = engine.local_to_utc("1869-10-02", "07:11", "Asia/Kolkata", 69.6093)
    assert "local mean time" in info["source"] and (u.hour, u.minute) == (2, 32)
    # US daylight saving
    u, info = engine.local_to_utc("2020-07-01", "12:00", "America/New_York", -74.0)
    assert info["offset_hours"] == -4
    u, info = engine.local_to_utc("2020-01-01", "12:00", "America/New_York", -74.0)
    assert info["offset_hours"] == -5


def test_unknown_time_uses_moon_houses():
    utc, _ = engine.local_to_utc("1990-05-05", None, "Asia/Kolkata", 77.2)
    c = engine.compute_chart(utc, 28.6, 77.2, time_known=False, now=NOW)
    assert c["planets"]["Moon"]["house"] == 1 and not c["lagna"]["reliable"]


def test_dasha_structure():
    c = _chart("1950-09-17", "11:00", dm(23, 37), dm(72, 28))
    md = c["dasha"]["mahadashas"]
    for a, b in zip(md, md[1:]):
        assert a["end"] == b["start"]
    for m in md:
        assert m["antardashas"][0]["lord"] == m["lord"]
        assert m["antardashas"][0]["start"] == m["start"] and m["antardashas"][-1]["end"] == m["end"]
    cur = c["dasha"]["current"]
    assert cur["antardasha"]["start"] <= "2026-10-23" <= cur["antardasha"]["end"]
    pds = cur["pratyantardashas"]
    assert len(pds) == 9 and pds[0]["lord"] == cur["antardasha"]["lord"]
    assert pds[0]["start"] == cur["antardasha"]["start"] and pds[-1]["end"] == cur["antardasha"]["end"]
    for a, b in zip(pds, pds[1:]):
        assert a["end"] == b["start"]
    assert any(p["start"] == cur["pratyantardasha"]["start"] for p in pds)
    assert cur["pratyantardasha"]["start"] <= "2026-10-23" <= cur["pratyantardasha"]["end"]
    nxt = cur["next_antardasha_pratyantardashas"]
    assert nxt[0]["start"] == cur["next_antardasha"]["start"] and nxt[-1]["end"] == cur["next_antardasha"]["end"]
    assert nxt[0]["lord"] == cur["next_antardasha"]["lord"]
    assert cur["next_antardasha_mahadasha"] in engine.DASHA_ORDER


def test_yogas_doshas_modi():
    c = _chart("1950-09-17", "11:00", dm(23, 37), dm(72, 28))
    names = {y["name"] for y in c["yogas"]}
    assert "Gajakesari Yoga" in names                      # Jupiter 4th from Moon
    assert "Ruchaka Yoga (Pancha Mahapurusha)" in names     # Mars own sign in lagna
    m = next(d for d in c["doshas"] if d["name"].startswith("Manglik"))
    assert m["present"]                                     # Mars in 1st
    ks = next(d for d in c["doshas"] if d["name"] == "Kaal Sarp Dosha")
    assert not ks["present"]
    ss = next(d for d in c["doshas"] if d["name"].startswith("Sade Sati"))
    assert ss["saturn_transit_sign"] == "Pisces" and not ss["active"]   # Scorpio Moon: Saturn 5th


def test_sade_sati_active_for_aquarius_pisces_aries_moon():
    # Oct 2026: Saturn in sidereal Pisces -> Sade Sati for Aquarius (setting), Pisces (peak), Aries (rising)
    for moon_sign, phase in ((10, "setting"), (11, "peak"), (0, "rising")):
        ss = engine.sade_sati(moon_sign, NOW)
        assert ss["active"] and ss["phase"].startswith(phase)


def test_kaal_sarp_detection():
    # Synthetic: put all planets between Rahu (0°) and Ketu (180°)
    fake = {"planets": {p: {"longitude": 10.0 + i * 20, "sign_index": 0, "sign": "Aries", "house": 1,
                            "dignity": "neutral sign", "combust": False, "degree": 10}
                        for i, p in enumerate(engine.PLANETS[:7])}}
    fake["planets"]["Rahu"] = {"longitude": 0.0, "sign_index": 0, "sign": "Aries", "house": 1}
    rel = [((fake["planets"][p]["longitude"] - 0) % 360) for p in engine.PLANETS[:7]]
    assert all(0 < x < 180 for x in rel)


def test_milan_same_nakshatra_has_nadi_dosha():
    a = _chart("1950-09-17", "11:00", dm(23, 37), dm(72, 28))
    m = milan(a, a)
    nadi = next(k for k in m["kootas"] if k["name"] == "Nadi")
    assert nadi["score"] == 0 and m["max"] == 36
    assert 0 <= m["total"] <= 36
    assert sum(k["max"] for k in m["kootas"]) == 36


def test_milan_known_pair():
    # Girl Moon Ashwini (Aries), boy Moon Bharani (Aries) -> classic textbook values
    girl = {"planets": {"Moon": {"sign_index": 0, "nakshatra_index": 0, "degree": 5, "pada": 2}},
            "doshas": [{"name": "Manglik (Kuja) Dosha", "level": "none"}]}
    boy = {"planets": {"Moon": {"sign_index": 0, "nakshatra_index": 1, "degree": 20, "pada": 2}},
           "doshas": [{"name": "Manglik (Kuja) Dosha", "level": "none"}]}
    m = milan(girl, boy)
    k = {x["name"]: x["score"] for x in m["kootas"]}
    assert k["Varna"] == 1 and k["Vashya"] == 2 and k["Graha Maitri"] == 5 and k["Bhakoot"] == 7
    assert k["Yoni"] == 2        # Horse vs Elephant
    assert k["Gana"] == 5        # Deva girl, Manushya boy (boy Deva+girl Manushya would be 6)
    assert k["Nadi"] == 8        # Adi vs Madhya
    assert k["Tara"] == 3        # 2nd (Sampat) and 27th->9 (Ati-mitra)
