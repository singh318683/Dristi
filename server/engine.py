"""Vedic (sidereal, Lahiri) chart engine built on Swiss Ephemeris.

Everything here is pure computation: give it a UTC instant + lat/lon and it
returns plain dicts that are JSON-serialisable (for the UI and the LLM prompt).
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import swisseph as swe

EPHE = os.path.join(os.path.dirname(__file__), "ephe")
swe.set_ephe_path(EPHE)
swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)
FLAGS = swe.FLG_SWIEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED

SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra",
         "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
SIGNS_HI = ["मेष", "वृषभ", "मिथुन", "कर्क", "सिंह", "कन्या", "तुला", "वृश्चिक", "धनु", "मकर", "कुंभ", "मीन"]
NAKSHATRAS = ["Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra", "Punarvasu",
              "Pushya", "Ashlesha", "Magha", "Purva Phalguni", "Uttara Phalguni", "Hasta",
              "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha", "Mula", "Purva Ashadha",
              "Uttara Ashadha", "Shravana", "Dhanishta", "Shatabhisha", "Purva Bhadrapada",
              "Uttara Bhadrapada", "Revati"]
PLANETS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu"]
ABBR = {"Sun": "Su", "Moon": "Mo", "Mars": "Ma", "Mercury": "Me", "Jupiter": "Ju",
        "Venus": "Ve", "Saturn": "Sa", "Rahu": "Ra", "Ketu": "Ke", "Lagna": "As"}
SWE_ID = {"Sun": swe.SUN, "Moon": swe.MOON, "Mars": swe.MARS, "Mercury": swe.MERCURY,
          "Jupiter": swe.JUPITER, "Venus": swe.VENUS, "Saturn": swe.SATURN, "Rahu": swe.MEAN_NODE}
SIGN_LORD = ["Mars", "Venus", "Mercury", "Moon", "Sun", "Mercury", "Venus", "Mars",
             "Jupiter", "Saturn", "Saturn", "Jupiter"]
# Vimshottari
DASHA_ORDER = ["Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter", "Saturn", "Mercury"]
DASHA_YEARS = {"Ketu": 7, "Venus": 20, "Sun": 6, "Moon": 10, "Mars": 7, "Rahu": 18,
               "Jupiter": 16, "Saturn": 19, "Mercury": 17}
YEAR_DAYS = 365.25
NAK_SPAN = 360 / 27
PADA_SPAN = NAK_SPAN / 4

EXALT = {"Sun": 0, "Moon": 1, "Mars": 9, "Mercury": 5, "Jupiter": 3, "Venus": 11,
         "Saturn": 6, "Rahu": 1, "Ketu": 7}
DEBIL = {k: (v + 6) % 12 for k, v in EXALT.items()}
OWN = {"Sun": [4], "Moon": [3], "Mars": [0, 7], "Mercury": [2, 5], "Jupiter": [8, 11],
       "Venus": [1, 6], "Saturn": [9, 10], "Rahu": [10], "Ketu": [7]}
# moolatrikona: sign, from-deg, to-deg
MOOLA = {"Sun": (4, 0, 20), "Moon": (1, 4, 30), "Mars": (0, 0, 12), "Mercury": (5, 16, 20),
         "Jupiter": (8, 0, 10), "Venus": (6, 0, 15), "Saturn": (10, 0, 20)}
# naisargika (natural) friendships
FRIENDS = {"Sun": ["Moon", "Mars", "Jupiter"], "Moon": ["Sun", "Mercury"],
           "Mars": ["Sun", "Moon", "Jupiter"], "Mercury": ["Sun", "Venus"],
           "Jupiter": ["Sun", "Moon", "Mars"], "Venus": ["Mercury", "Saturn"],
           "Saturn": ["Mercury", "Venus"], "Rahu": ["Mercury", "Venus", "Saturn"],
           "Ketu": ["Mars", "Venus", "Saturn"]}
ENEMIES = {"Sun": ["Venus", "Saturn"], "Moon": [], "Mars": ["Mercury"], "Mercury": ["Moon"],
           "Jupiter": ["Mercury", "Venus"], "Venus": ["Sun", "Moon"],
           "Saturn": ["Sun", "Moon", "Mars"], "Rahu": ["Sun", "Moon", "Mars"],
           "Ketu": ["Sun", "Moon"]}
# special graha drishti (house offsets counted inclusively; 7th is universal)
ASPECTS = {"Mars": [4, 7, 8], "Jupiter": [5, 7, 9], "Saturn": [3, 7, 10],
           "Rahu": [5, 7, 9], "Ketu": [5, 7, 9]}
COMBUST_ORB = {"Moon": 12, "Mars": 17, "Mercury": 14, "Jupiter": 11, "Venus": 10, "Saturn": 15}
COMBUST_ORB_RETRO = {"Mercury": 12, "Venus": 8}
HOUSE_MEANING = {1: "self, body, personality", 2: "wealth, family, speech", 3: "courage, siblings, effort",
                 4: "home, mother, comforts, property", 5: "children, intellect, romance, education",
                 6: "health issues, enemies, debts, service", 7: "marriage, partnerships",
                 8: "longevity, transformation, sudden events, inheritance", 9: "fortune, dharma, father, higher learning",
                 10: "career, status, karma", 11: "gains, income, friends, wishes", 12: "losses, expenses, foreign lands, moksha"}


# ---------------------------------------------------------------- time ----
def local_to_utc(date: str, time: str | None, tz: str, lon: float,
                 offset_override: float | None = None) -> tuple[datetime, dict]:
    """date 'YYYY-MM-DD', time 'HH:MM[:SS]' or None (unknown -> 12:00).
    Uses tzdb history (war time, DST). If tzdb only knows Local Mean Time for
    that date (or India before IST was adopted in 1906 / Railway time eras),
    we use true LMT from the longitude, which is what astrologers use."""
    y, m, d = map(int, date.split("-"))
    if time:
        parts = [int(float(p)) for p in time.split(":")]
        hh, mm, ss = (parts + [0, 0])[:3]
    else:
        hh, mm, ss = 12, 0, 0
    naive = datetime(y, m, d, hh, mm, ss)
    info = {"tz": tz}
    if offset_override is not None:
        off = timedelta(hours=offset_override)
        info.update(offset_hours=offset_override, source="manual")
    else:
        z = ZoneInfo(tz)
        aware = naive.replace(tzinfo=z)
        abbr = aware.tzname() or ""
        pre_std = abbr == "LMT" or (tz in ("Asia/Kolkata", "Asia/Calcutta") and y < 1906) \
            or (tz == "Asia/Karachi" and y < 1907) or (tz == "Asia/Dhaka" and y < 1890)
        if pre_std:
            off = timedelta(hours=lon / 15.0)
            info.update(offset_hours=round(lon / 15.0, 4), source="local mean time (pre-standard-time)")
        else:
            off = aware.utcoffset()
            info.update(offset_hours=off.total_seconds() / 3600, source=f"tzdb ({abbr})")
    utc = (naive - off).replace(tzinfo=timezone.utc)
    return utc, info


def jd_ut(dt: datetime) -> float:
    dt = dt.astimezone(timezone.utc)
    return swe.julday(dt.year, dt.month, dt.day,
                      dt.hour + dt.minute / 60 + (dt.second + dt.microsecond / 1e6) / 3600)


def jd_to_dt(jd: float) -> datetime:
    y, m, d, h = swe.revjul(jd)
    return datetime(y, m, d, tzinfo=timezone.utc) + timedelta(hours=h)


# ----------------------------------------------------------- positions ----
def nak_info(lon: float) -> dict:
    i = int(lon // NAK_SPAN) % 27
    pada = int((lon % NAK_SPAN) // PADA_SPAN) + 1
    return {"nakshatra": NAKSHATRAS[i], "nakshatra_index": i, "pada": pada,
            "nakshatra_lord": DASHA_ORDER[i % 9]}


def dms(x: float) -> str:
    d = int(x); mf = (x - d) * 60; m = int(mf); s = int(round((mf - m) * 60))
    if s == 60: m, s = m + 1, 0
    if m == 60: d, m = d + 1, 0
    return f"{d}°{m:02d}'{s:02d}\""


def sidereal_positions(jd: float) -> dict:
    out = {}
    for p in PLANETS[:-1]:
        xx, _ = swe.calc_ut(jd, SWE_ID[p], FLAGS)
        out[p] = (xx[0] % 360, xx[3])
    r_lon, r_spd = out["Rahu"]
    out["Ketu"] = ((r_lon + 180) % 360, r_spd)
    return out


def ayanamsa(jd: float) -> float:
    return swe.get_ayanamsa_ut(jd)


def lagna_lon(jd: float, lat: float, lon: float) -> float:
    _, ascmc = swe.houses_ex(jd, lat, lon, b"W", swe.FLG_SIDEREAL)
    return ascmc[0] % 360


def dignity(planet: str, sign: int, deg: float) -> str:
    moola = MOOLA.get(planet)
    if moola and sign == moola[0] and moola[1] <= deg < moola[2]:
        return "moolatrikona"
    # Mercury is exalted in Virgo and also owns Virgo. Past the moolatrikona
    # slice (16°–20°) the rest of Virgo is own sign, not exalted (BPHS).
    if EXALT.get(planet) == sign:
        if moola and sign == moola[0] and deg >= moola[2] and sign in OWN.get(planet, []):
            return "own sign"
        return "exalted"
    if DEBIL.get(planet) == sign:
        return "debilitated"
    if sign in OWN.get(planet, []):
        return "own sign"
    lord = SIGN_LORD[sign]
    if lord == planet:
        return "own sign"
    if lord in FRIENDS.get(planet, []):
        return "friendly sign"
    if lord in ENEMIES.get(planet, []):
        return "enemy sign"
    return "neutral sign"


def navamsa_sign(lon: float) -> int:
    return int(lon // (30 / 9)) % 12


def dashamsha_sign(lon: float) -> int:
    """D10 (Parashara): 3° parts. Odd signs count from themselves, even signs from the 9th."""
    sign = int(lon // 30) % 12
    part = min(9, int((lon % 30) // 3))
    # sign index 0,2,4... are the odd rashis (Aries, Gemini, ...)
    start = sign if sign % 2 == 0 else (sign + 8) % 12
    return (start + part) % 12


# -------------------------------------------------------------- dashas ----
def vimshottari(moon_lon: float, birth: datetime, now: datetime, levels: int = 3) -> dict:
    i = int(moon_lon // NAK_SPAN)
    frac = (moon_lon % NAK_SPAN) / NAK_SPAN
    lord0 = DASHA_ORDER[i % 9]
    start_idx = DASHA_ORDER.index(lord0)
    # first MD started before birth
    md_start = birth - timedelta(days=frac * DASHA_YEARS[lord0] * YEAR_DAYS)
    balance_years = (1 - frac) * DASHA_YEARS[lord0]

    def subs(lord, start, years):
        k = DASHA_ORDER.index(lord)
        t = start; res = []
        for j in range(9):
            l = DASHA_ORDER[(k + j) % 9]
            dur = years * DASHA_YEARS[l] / 120
            end = t + timedelta(days=dur * YEAR_DAYS)
            res.append({"lord": l, "start": t, "end": end, "years": dur})
            t = end
        return res

    mds = []
    t = md_start
    for j in range(10):
        l = DASHA_ORDER[(start_idx + j) % 9]
        end = t + timedelta(days=DASHA_YEARS[l] * YEAR_DAYS)
        mds.append({"lord": l, "start": t, "end": end, "years": DASHA_YEARS[l]})
        t = end

    def fmt(p, with_subs=False):
        d = {"lord": p["lord"], "start": p["start"].date().isoformat(), "end": p["end"].date().isoformat()}
        return d

    current = {}
    md = next((p for p in mds if p["start"] <= now < p["end"]), None)
    timeline = []
    for p in mds:
        if p["end"] < birth:
            continue
        e = fmt(p)
        e["antardashas"] = [fmt(a) for a in subs(p["lord"], p["start"], p["years"])]
        timeline.append(e)
    if md:
        current["mahadasha"] = fmt(md)
        ads = subs(md["lord"], md["start"], md["years"])
        ad = next(a for a in ads if a["start"] <= now < a["end"])
        current["antardasha"] = fmt(ad)
        pds = subs(ad["lord"], ad["start"], ad["years"])
        pd = next(a for a in pds if a["start"] <= now < a["end"])
        current["pratyantardasha"] = fmt(pd)
        current["pratyantardashas"] = [fmt(x) for x in pds]
        ad_i = ads.index(ad)
        if ad_i + 1 < len(ads):
            nxt, nxt_md = ads[ad_i + 1], md["lord"]
        else:
            md_i = mds.index(md)
            if md_i + 1 < len(mds):
                nm = mds[md_i + 1]
                nxt, nxt_md = subs(nm["lord"], nm["start"], nm["years"])[0], nm["lord"]
            else:
                nxt = nxt_md = None
        if nxt is not None:
            current["next_antardasha"] = fmt(nxt)
            current["next_antardasha_mahadasha"] = nxt_md
            current["next_antardasha_pratyantardashas"] = [
                fmt(x) for x in subs(nxt["lord"], nxt["start"], nxt["years"])]
    by, bm = int(balance_years), (balance_years - int(balance_years)) * 12
    return {"system": "Vimshottari", "year_length_days": YEAR_DAYS,
            "birth_nakshatra_lord": lord0,
            "balance_at_birth": f"{lord0} {by}y {int(bm)}m {int((bm - int(bm)) * 30.4375)}d",
            "current": current, "mahadashas": timeline}


# --------------------------------------------------------------- chart ----
def planet_rows(pos: dict, lagna_sign: int, sun_lon: float) -> dict:
    rows = {}
    for p in PLANETS:
        lon, spd = pos[p]
        sign = int(lon // 30); deg = lon % 30
        retro = spd < 0 if p not in ("Rahu", "Ketu") else True
        combust = False
        if p in COMBUST_ORB:
            dist = abs((lon - sun_lon + 180) % 360 - 180)
            orb = COMBUST_ORB_RETRO.get(p, COMBUST_ORB[p]) if spd < 0 else COMBUST_ORB[p]
            combust = dist <= orb
        rows[p] = {"longitude": round(lon, 4), "sign": SIGNS[sign], "sign_index": sign,
                   "degree": round(deg, 4), "degree_dms": dms(deg), **nak_info(lon),
                   "retrograde": retro, "combust": combust,
                   "house": (sign - lagna_sign) % 12 + 1, "dignity": dignity(p, sign, deg),
                   "navamsa_sign": SIGNS[navamsa_sign(lon)],
                   "dashamsha_sign": SIGNS[dashamsha_sign(lon)],
                   "speed_deg_per_day": round(spd, 4)}
    return rows


def aspects_of(planet: str, house: int) -> list[int]:
    return sorted(((house - 1 + k - 1) % 12) + 1 for k in ASPECTS.get(planet, [7]))


def chart_houses(rows: dict, lagna_sign: int) -> list[dict]:
    houses = []
    for h in range(1, 13):
        s = (lagna_sign + h - 1) % 12
        lord = SIGN_LORD[s]
        occ = [p for p in PLANETS if rows[p]["house"] == h]
        asp = [p for p in PLANETS if h in aspects_of(p, rows[p]["house"])]
        houses.append({"house": h, "sign": SIGNS[s], "sign_index": s, "lord": lord,
                       "lord_in_house": rows[lord]["house"], "lord_in_sign": rows[lord]["sign"],
                       "occupants": occ, "aspected_by": asp, "signifies": HOUSE_MEANING[h]})
    return houses


def compute_chart(utc: datetime, lat: float, lon: float, time_known: bool = True,
                  now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    jd = jd_ut(utc)
    pos = sidereal_positions(jd)
    asc = lagna_lon(jd, lat, lon)
    moon_sign = int(pos["Moon"][0] // 30)
    # with unknown time, houses are counted from the Moon (Chandra lagna)
    ref_sign = int(asc // 30) if time_known else moon_sign
    rows = planet_rows(pos, ref_sign, pos["Sun"][0])
    lagna = {"longitude": round(asc, 4), "sign": SIGNS[int(asc // 30)], "sign_index": int(asc // 30),
             "degree": round(asc % 30, 4), "degree_dms": dms(asc % 30), **nak_info(asc),
             "navamsa_sign": SIGNS[navamsa_sign(asc)],
             "dashamsha_sign": SIGNS[dashamsha_sign(asc)], "reliable": time_known}
    houses = chart_houses(rows, ref_sign)
    d1 = {SIGNS[s]: [p for p in PLANETS if rows[p]["sign_index"] == s] for s in range(12)}
    d9 = {SIGNS[s]: [p for p in PLANETS if navamsa_sign(pos[p][0]) == s] for s in range(12)}
    for p in PLANETS:
        rows[p]["aspects_houses"] = aspects_of(p, rows[p]["house"])
    chart = {
        "ayanamsa": {"name": "Lahiri (Chitrapaksha)", "value": round(ayanamsa(jd), 6)},
        "node_type": "mean", "house_system": "whole sign",
        "time_known": time_known,
        "house_reference": "Lagna" if time_known else "Moon (Chandra lagna) since birth time is unknown",
        "birth_utc": utc.isoformat(), "julian_day_ut": round(jd, 6),
        "lagna": lagna, "planets": rows, "houses": houses,
        "moon_sign": SIGNS[moon_sign], "sun_sign": rows["Sun"]["sign"],
        "janma_nakshatra": rows["Moon"]["nakshatra"], "janma_pada": rows["Moon"]["pada"],
        "d1": {"lagna_sign": SIGNS[ref_sign], "signs": d1},
        "d9": {"lagna_sign": SIGNS[navamsa_sign(asc)] if time_known else SIGNS[navamsa_sign(pos['Moon'][0])],
               "signs": d9},
        "dasha": vimshottari(pos["Moon"][0], utc, now),
    }
    chart["transits"] = transits(now, ref_sign, moon_sign)
    chart["yogas"], chart["doshas"] = yogas_and_doshas(chart, now)
    return chart


# ------------------------------------------------------------ transits ----
def transits(now: datetime, ref_sign: int, moon_sign: int) -> dict:
    jd = jd_ut(now)
    pos = sidereal_positions(jd)
    out = {}
    for p in PLANETS:
        lon, spd = pos[p]
        s = int(lon // 30)
        out[p] = {"sign": SIGNS[s], "degree": round(lon % 30, 2), **nak_info(lon),
                  "retrograde": spd < 0 if p not in ("Rahu", "Ketu") else True,
                  "house_from_lagna": (s - ref_sign) % 12 + 1,
                  "house_from_moon": (s - moon_sign) % 12 + 1}
    return {"as_of_utc": now.replace(microsecond=0).isoformat(), "planets": out}


def sign_changes(planet: str, start: datetime, years: float, step_days: float = 4) -> list[tuple[datetime, int]]:
    """Ingress times (sidereal) of a slow planet within [start, start+years]."""
    res = []
    jd = jd_ut(start); end = jd + years * 365.25
    sign = int(sidereal_positions(jd)[planet][0] // 30)
    while jd < end:
        jd2 = jd + step_days
        s2 = int(swe.calc_ut(jd2, SWE_ID[planet], FLAGS)[0][0] // 30)
        if s2 != sign:
            a, b = jd, jd2
            for _ in range(30):
                mid = (a + b) / 2
                if int(swe.calc_ut(mid, SWE_ID[planet], FLAGS)[0][0] // 30) == sign:
                    a = mid
                else:
                    b = mid
            res.append((jd_to_dt(b), s2))
            sign = s2
        jd = jd2
    return res


def slow_ingresses(now: datetime, years: float = 2.0) -> list[dict]:
    """Sidereal sign ingresses of Jupiter, Saturn, and Rahu in the next `years` (includes retrograde returns)."""
    out = []
    for planet in ("Jupiter", "Saturn", "Rahu"):
        for dt, sign in sign_changes(planet, now, years, step_days=4):
            out.append({"planet": planet, "enters": SIGNS[sign], "date": dt.date().isoformat()})
    out.sort(key=lambda r: (r["date"], r["planet"]))
    return out


def sade_sati(moon_sign: int, now: datetime) -> dict:
    """Status + approximate window of the Sade Sati around `now` (Saturn in 12th/1st/2nd from Moon)."""
    sat = int(sidereal_positions(jd_ut(now))["Saturn"][0] // 30)
    rel = (sat - moon_sign) % 12 + 1
    phases = {12: "rising (first 2.5 yrs, Saturn in 12th from Moon)",
              1: "peak (Saturn over natal Moon sign)", 2: "setting (last 2.5 yrs, Saturn in 2nd from Moon)"}
    active = rel in phases
    window_signs = {(moon_sign - 1) % 12, moon_sign, (moon_sign + 1) % 12}
    ev = sign_changes("Saturn", now - timedelta(days=365.25 * 8), 40, step_days=6)
    # build intervals of Saturn being in window signs
    sat_then = int(sidereal_positions(jd_ut(now - timedelta(days=365.25 * 8)))["Saturn"][0] // 30)
    intervals = []; cur_in = sat_then in window_signs; cur_start = now - timedelta(days=365.25 * 8) if cur_in else None
    for t, s in ev:
        inside = s in window_signs
        if inside and not cur_in:
            cur_start = t
        if not inside and cur_in:
            intervals.append((cur_start, t))
        cur_in = inside
    # merge short exits caused by retrograde back-and-forth (< 1.5 years)
    merged = []
    for a, b in intervals:
        if merged and (a - merged[-1][1]).days < 550:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    cur = next((iv for iv in merged if iv[0] <= now <= iv[1]), None)
    nxt = next((iv for iv in merged if iv[0] > now), None)
    first_partial = merged and merged[0][0] <= now - timedelta(days=365.25 * 8) + timedelta(days=1)
    dhaiya = {4: "Kantaka Shani / Ardhashtama (Saturn 4th from Moon)", 8: "Ashtama Shani (Saturn 8th from Moon)"}
    res = {"active": active, "saturn_transit_sign": SIGNS[sat], "saturn_house_from_moon": rel,
           "phase": phases.get(rel), "small_panoti_dhaiya": dhaiya.get(rel)}
    if cur:
        res["current_window"] = {"start": None if (first_partial and cur == merged[0]) else cur[0].date().isoformat(),
                                 "end": cur[1].date().isoformat()}
    if nxt:
        res["next_window"] = {"start": nxt[0].date().isoformat(), "end": nxt[1].date().isoformat()}
    return res


# --------------------------------------------------------------- yogas ----
KENDRA = {1, 4, 7, 10}
TRIKONA = {1, 5, 9}
DUSTHANA = {6, 8, 12}


def lords_of(chart: dict, planet: str) -> list[int]:
    return [h["house"] for h in chart["houses"] if h["lord"] == planet]


def yogas_and_doshas(chart: dict, now: datetime):
    P = chart["planets"]; H = {h["house"]: h for h in chart["houses"]}
    yogas, doshas = [], []
    tk = chart["time_known"]
    hfrom = lambda a, b: (P[b]["sign_index"] - P[a]["sign_index"]) % 12 + 1  # house of b counted from a

    # Gajakesari: Jupiter in a kendra from Moon
    gk = hfrom("Moon", "Jupiter")
    if gk in KENDRA:
        strength = "strong" if P["Jupiter"]["dignity"] not in ("debilitated", "enemy sign") and not P["Jupiter"]["combust"] else "weakened"
        yogas.append({"name": "Gajakesari Yoga", "present": True, "strength": strength,
                      "basis": f"Jupiter in {P['Jupiter']['sign']} is {gk}{_ord(gk)} from Moon in {P['Moon']['sign']}"})
    # Budh-Aditya
    if P["Sun"]["sign"] == P["Mercury"]["sign"]:
        yogas.append({"name": "Budhaditya Yoga", "present": True,
                      "strength": "weakened (Mercury combust)" if P["Mercury"]["combust"] else "normal",
                      "basis": f"Sun and Mercury together in {P['Sun']['sign']} (house {P['Sun']['house']})"})
    if P["Moon"]["sign"] == P["Mars"]["sign"] or hfrom("Moon", "Mars") == 7:
        yogas.append({"name": "Chandra-Mangala Yoga", "present": True,
                      "basis": f"Moon and Mars {'together in ' + P['Moon']['sign'] if P['Moon']['sign'] == P['Mars']['sign'] else 'in mutual 7th aspect'}"})
    # Pancha Mahapurusha
    pmp = {"Mars": "Ruchaka", "Mercury": "Bhadra", "Jupiter": "Hamsa", "Venus": "Malavya", "Saturn": "Shasha"}
    for p, n in pmp.items():
        if P[p]["house"] in KENDRA and P[p]["dignity"] in ("exalted", "own sign", "moolatrikona"):
            yogas.append({"name": f"{n} Yoga (Pancha Mahapurusha)", "present": True,
                          "basis": f"{p} {P[p]['dignity']} in {P[p]['sign']} in house {P[p]['house']} (a kendra)"})
    # Raja yogas: kendra lord + trikona lord associated (conjunction, mutual aspect, exchange)
    if tk:
        seen = set()
        kl = {H[h]["lord"] for h in (4, 7, 10)} | {H[1]["lord"]}
        tl = {H[h]["lord"] for h in (5, 9)} | {H[1]["lord"]}
        for p in PLANETS[:7]:
            ph = lords_of(chart, p)
            if any(h in (4, 7, 10) for h in ph) and any(h in (5, 9) for h in ph):
                yogas.append({"name": "Yogakaraka", "present": True,
                              "basis": f"{p} rules both a kendra and a trikona (houses {', '.join(map(str, ph))}) — placed in house {P[p]['house']}"})
        for a in kl:
            for b in tl:
                if a == b or (b, a) in seen:
                    continue
                seen.add((a, b))
                ha = [h for h in lords_of(chart, a) if h in KENDRA]; hb = [h for h in lords_of(chart, b) if h in TRIKONA]
                if ha == [1] and hb == [1]:
                    continue
                how = None
                if P[a]["sign"] == P[b]["sign"]:
                    how = f"conjunct in {P[a]['sign']} (house {P[a]['house']})"
                elif P[a]["sign_index"] in OWN.get(b, []) and P[b]["sign_index"] in OWN.get(a, []):
                    how = "exchange of signs (parivartana)"
                elif P[a]["house"] in aspects_of(b, P[b]["house"]) and P[b]["house"] in aspects_of(a, P[a]["house"]):
                    how = "mutual aspect"
                if how:
                    yogas.append({"name": "Raja Yoga (kendra-trikona)", "present": True,
                                  "basis": f"{a} (lord of {','.join(map(str, ha))}) and {b} (lord of {','.join(map(str, hb))}) {how}"})
        # Dhana yoga: lords of 2/11 with 1/5/9
        for a in {H[2]["lord"], H[11]["lord"]}:
            for b in {H[1]["lord"], H[5]["lord"], H[9]["lord"]}:
                if a != b and P[a]["sign"] == P[b]["sign"]:
                    yogas.append({"name": "Dhana Yoga", "present": True,
                                  "basis": f"wealth lord {a} conjunct {b} in {P[a]['sign']}"})
        # Viparita raja yoga
        # Viparita raja yoga: lord of a dusthana (6/8/12) placed in a dusthana
        names = {6: "Harsha", 8: "Sarala", 12: "Vimala"}
        for h in (6, 8, 12):
            l = H[h]["lord"]
            if P[l]["house"] in DUSTHANA and not (set(lords_of(chart, l)) & (KENDRA | TRIKONA)):
                yogas.append({"name": f"Viparita Raja Yoga ({names[h]})", "present": True,
                              "basis": f"lord of {h} ({l}) placed in house {P[l]['house']}"})
    # Neecha bhanga (simple: debilitated planet's dispositor or exaltation lord in kendra from lagna/Moon)
    for p in PLANETS[:7]:
        if P[p]["dignity"] == "debilitated":
            disp = SIGN_LORD[P[p]["sign_index"]]
            exl = SIGN_LORD[EXALT[p]]
            cond = [q for q in {disp, exl} if P[q]["house"] in KENDRA or hfrom("Moon", q) in KENDRA]
            if cond:
                yogas.append({"name": "Neecha Bhanga (cancellation of debility)", "present": True,
                              "basis": f"{p} debilitated in {P[p]['sign']}, but {', '.join(cond)} in a kendra from Lagna/Moon"})

    # ---- doshas
    # Manglik: whole-sign houses of Mars from Lagna, Moon and Venus. 1, 2, 4, 7, 8, 12.
    doshas.append(_manglik_dosha(P, tk, hfrom))
    # Kaal Sarp
    r = P["Rahu"]["longitude"]
    rel = [((P[p]["longitude"] - r) % 360) for p in PLANETS[:7]]
    side_a = all(0 < x < 180 for x in rel); side_b = all(180 < x < 360 for x in rel)
    ks_names = {1: "Anant", 2: "Kulik", 3: "Vasuki", 4: "Shankhapal", 5: "Padma", 6: "Mahapadma",
                7: "Takshak", 8: "Karkotak", 9: "Shankhachood", 10: "Ghatak", 11: "Vishdhar", 12: "Sheshnag"}
    ks = {"name": "Kaal Sarp Dosha", "present": side_a or side_b}
    if ks["present"]:
        ks["type"] = (ks_names[P["Rahu"]["house"]] + " Kaal Sarp") if tk else "Kaal Sarp"
        ks["basis"] = "all seven planets are hemmed between Rahu and Ketu"
    else:
        minority = sum(1 for y in rel if y > 180) < 4
        outside = [PLANETS[i] for i, x in enumerate(rel) if (x > 180) == minority]
        ks["basis"] = f"not formed: {', '.join(outside)} on the other side of the Rahu–Ketu axis"
    doshas.append(ks)
    # Sade Sati
    ss = sade_sati(P["Moon"]["sign_index"], now)
    doshas.append({"name": "Sade Sati (Saturn transit)", "present": ss["active"], **ss})
    return yogas, doshas


def _ord(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _join(bits: list[str], conj: str) -> str:
    if len(bits) <= 1:
        return bits[0] if bits else ""
    if len(bits) == 2:
        return f"{bits[0]} {conj} {bits[1]}"
    return ", ".join(bits[:-1]) + f", {conj} " + bits[-1]


def _manglik_dosha(P: dict, time_known: bool, hfrom) -> dict:
    """Label names only the references whose house is actually 1, 2, 4, 7, 8 or 12."""
    bad = {1, 2, 4, 7, 8, 12}
    checked = []
    if time_known:
        checked.append(("Lagna", P["Mars"]["house"]))
    checked.append(("Moon", hfrom("Moon", "Mars")))
    checked.append(("Venus", hfrom("Venus", "Mars")))
    houses = {ref: house for ref, house in checked}
    sources = [ref for ref, house in checked if house in bad]
    missed = [(ref, house) for ref, house in checked if house not in bad]
    cancels = []
    if P["Mars"]["dignity"] in ("own sign", "exalted", "moolatrikona"):
        cancels.append(f"Mars is {P['Mars']['dignity']} in {P['Mars']['sign']}")
    if P["Mars"]["sign"] in ("Leo", "Aquarius"):
        cancels.append("Mars in Leo/Aquarius (classical exception)")
    if P["Jupiter"]["sign"] == P["Mars"]["sign"] or P["Mars"]["house"] in aspects_of("Jupiter", P["Jupiter"]["house"]):
        cancels.append("Jupiter conjoins/aspects Mars")
    from_lagna = time_known and houses.get("Lagna") in bad
    from_moon_as_lagna = (not time_known) and houses["Moon"] in bad
    if from_lagna or from_moon_as_lagna:
        level = "mild" if cancels else "present"
    elif sources:
        level = "partial"
    else:
        level = "none"
    hi_ref = {"Lagna": "लग्न", "Moon": "चंद्र", "Venus": "शुक्र"}
    if sources:
        en_bits = [f"{h}{_ord(h)} from the Lagna" if ref == "Lagna" else f"{h}{_ord(h)} from {ref}"
                   for ref, h in checked if ref in sources]
        hi_bits = [f"{hi_ref[ref]} से {h}वें भाव" for ref, h in checked if ref in sources]
        prefix = {"present": "present", "mild": "mild", "partial": "partial"}[level]
        hi_prefix = {"present": "पूरा", "mild": "हल्का", "partial": "आंशिक"}[level]
        label = f"{prefix}: Mars is {_join(en_bits, 'and')}"
        label_hi = f"{hi_prefix}: मंगल {_join(hi_bits, 'और')} में है"
    else:
        who = "the Lagna, Moon or Venus" if time_known else "the Moon or Venus"
        who_hi = "लग्न, चंद्र या शुक्र" if time_known else "चंद्र या शुक्र"
        label = f"Mars is not in houses 1, 2, 4, 7, 8 or 12 from {who}"
        label_hi = f"मंगल {who_hi} से 1, 2, 4, 7, 8 या 12वें भाव में नहीं है"
    if missed:
        miss_en = [f"{ref} ({h}{_ord(h)})" for ref, h in missed]
        miss_hi = [f"{hi_ref[ref]} ({h}वें)" for ref, h in missed]
        not_from = "not from " + _join(miss_en, "or")
        not_from_hi = _join(miss_hi, "या") + " से नहीं"
    else:
        not_from = ""
        not_from_hi = ""
    say = "Attribute Manglik only to sources, and quote label. " + (
        not_from[0].upper() + not_from[1:] + "." if not_from else "Every checked reference qualifies.")
    return {"name": "Manglik (Kuja) Dosha", "present": level != "none",
            "level": level, "sources": sources, "from": sources, "houses": houses,
            "label": label, "label_hi": label_hi, "not_from": not_from, "not_from_hi": not_from_hi,
            "say": say, "mitigations": cancels,
            "note": "Matching with another Manglik chart traditionally neutralises it."}


def moon_sign_now(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return transits(now, 0, 0)
