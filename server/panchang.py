"""Daily panchang from Swiss Ephemeris (Lahiri sidereal longitudes, apparent sunrise).

Tithi, yoga and karana use the Sun–Moon elongation, which is the same in a sidereal
or tropical zodiac. Nakshatra and the civil day use the sidereal Moon and local sunrise.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import swisseph as swe

import engine

# Weekday index is datetime.weekday(): Monday 0 … Sunday 6.
# Rahu kaal is one eighth of the sunrise-to-sunset day. The eighth (1-based) is:
# Sun 8, Mon 2, Tue 7, Wed 5, Thu 6, Fri 4, Sat 3.
RAHU_EIGHTH = (2, 7, 5, 6, 4, 3, 8)

VAAR_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
VAAR_HI = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]

TITHI_EN = [
    "Shukla Pratipada", "Shukla Dwitiya", "Shukla Tritiya", "Shukla Chaturthi", "Shukla Panchami",
    "Shukla Shashthi", "Shukla Saptami", "Shukla Ashtami", "Shukla Navami", "Shukla Dashami",
    "Shukla Ekadashi", "Shukla Dwadashi", "Shukla Trayodashi", "Shukla Chaturdashi", "Purnima",
    "Krishna Pratipada", "Krishna Dwitiya", "Krishna Tritiya", "Krishna Chaturthi", "Krishna Panchami",
    "Krishna Shashthi", "Krishna Saptami", "Krishna Ashtami", "Krishna Navami", "Krishna Dashami",
    "Krishna Ekadashi", "Krishna Dwadashi", "Krishna Trayodashi", "Krishna Chaturdashi", "Amavasya",
]
TITHI_HI = [
    "शुक्ल प्रतिपदा", "शुक्ल द्वितीया", "शुक्ल तृतीया", "शुक्ल चतुर्थी", "शुक्ल पंचमी",
    "शुक्ल षष्ठी", "शुक्ल सप्तमी", "शुक्ल अष्टमी", "शुक्ल नवमी", "शुक्ल दशमी",
    "शुक्ल एकादशी", "शुक्ल द्वादशी", "शुक्ल त्रयोदशी", "शुक्ल चतुर्दशी", "पूर्णिमा",
    "कृष्ण प्रतिपदा", "कृष्ण द्वितीया", "कृष्ण तृतीया", "कृष्ण चतुर्थी", "कृष्ण पंचमी",
    "कृष्ण षष्ठी", "कृष्ण सप्तमी", "कृष्ण अष्टमी", "कृष्ण नवमी", "कृष्ण दशमी",
    "कृष्ण एकादशी", "कृष्ण द्वादशी", "कृष्ण त्रयोदशी", "कृष्ण चतुर्दशी", "अमावस्या",
]

YOGA_EN = [
    "Vishkambha", "Priti", "Ayushman", "Saubhagya", "Shobhana", "Atiganda", "Sukarma", "Dhriti",
    "Shula", "Ganda", "Vriddhi", "Dhruva", "Vyaghata", "Harshana", "Vajra", "Siddhi", "Vyatipata",
    "Variyan", "Parigha", "Shiva", "Siddha", "Sadhya", "Shubha", "Shukla", "Brahma", "Indra", "Vaidhriti",
]
YOGA_HI = [
    "विष्कम्भ", "प्रीति", "आयुष्मान", "सौभाग्य", "शोभन", "अतिगण्ड", "सुकर्मा", "धृति",
    "शूल", "गण्ड", "वृद्धि", "ध्रुव", "व्याघात", "हर्षण", "वज्र", "सिद्धि", "व्यतीपात",
    "वरीयान", "परिघ", "शिव", "सिद्ध", "साध्य", "शुभ", "शुक्ल", "ब्रह्म", "इन्द्र", "वैधृति",
]

NAK_HI = [
    "अश्विनी", "भरणी", "कृत्तिका", "रोहिणी", "मृगशिरा", "आर्द्रा", "पुनर्वसु", "पुष्य", "अश्लेषा",
    "मघा", "पूर्व फाल्गुनी", "उत्तर फाल्गुनी", "हस्त", "चित्रा", "स्वाती", "विशाखा", "अनुराधा",
    "ज्येष्ठा", "मूल", "पूर्वाषाढ़ा", "उत्तराषाढ़ा", "श्रवण", "धनिष्ठा", "शतभिषा", "पूर्व भाद्रपद",
    "उत्तर भाद्रपद", "रेवती",
]

KARANA_REPEAT = ["Bava", "Balava", "Kaulava", "Taitila", "Gara", "Vanija", "Vishti"]
KARANA_REPEAT_HI = ["बव", "बालव", "कौलव", "तैतिल", "गर", "वणिज", "विष्टि"]
KARANA_FIXED = {0: ("Kimstughna", "किंस्तुघ्न"), 57: ("Shakuni", "शकुनि"), 58: ("Chatushpada", "चतुष्पाद"), 59: ("Naga", "नाग")}


def tithi_index(elongation: float) -> int:
    """0..29 from the Moon-minus-Sun elongation in degrees."""
    return int((elongation % 360) // 12) % 30


def yoga_index(sun_lon: float, moon_lon: float) -> int:
    return int(((sun_lon + moon_lon) % 360) // engine.NAK_SPAN) % 27


def karana_index(elongation: float) -> int:
    """0..59. Each karana is half a tithi (6°)."""
    return int((elongation % 360) // 6) % 60


def karana_names(index: int) -> tuple[str, str]:
    if index in KARANA_FIXED:
        return KARANA_FIXED[index]
    i = (index - 1) % 7
    return KARANA_REPEAT[i], KARANA_REPEAT_HI[i]


def _angles(jd: float) -> tuple[float, float]:
    pos = engine.sidereal_positions(jd)
    return pos["Sun"][0], pos["Moon"][0]


def _elongation(jd: float) -> float:
    sun, moon = _angles(jd)
    return (moon - sun) % 360


def _sun_event(jd_start: float, lon: float, lat: float, rise: bool) -> float:
    flag = swe.CALC_RISE if rise else swe.CALC_SET
    # Upper limb with standard refraction: the sunrise/sunset used for panchang and Rahu kaal.
    res, tret = swe.rise_trans(jd_start, swe.SUN, flag, (lon, lat, 0.0), 1013.25, 15.0)
    if res != 0:
        raise ValueError("The Sun does not rise and set normally at this place on this date")
    return tret[0]


def _until(jd: float, same, horizon_days: float = 2.0) -> float | None:
    """First instant after jd where same(jd) becomes false, or None."""
    if not same(jd):
        return jd
    step = 20 / 1440  # 20 minutes
    end = jd + horizon_days
    t = jd
    while t < end:
        t2 = min(end, t + step)
        if not same(t2):
            a, b = t, t2
            for _ in range(24):
                mid = (a + b) / 2
                if same(mid):
                    a = mid
                else:
                    b = mid
            return b
        t = t2
    return None


def _iso(jd: float, tz: ZoneInfo) -> str:
    dt = engine.jd_to_dt(jd).astimezone(tz).replace(microsecond=0)
    return dt.isoformat()


def panchang(date: str, lat: float, lon: float, tz: str) -> dict:
    """Panchang for the local civil date, elements taken at that morning's sunrise."""
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("latitude or longitude is out of range")
    zone = ZoneInfo(tz)
    y, m, d = (int(x) for x in date.split("-"))
    local_midnight = datetime(y, m, d, tzinfo=zone)
    jd0 = engine.jd_ut(local_midnight.astimezone(timezone.utc))
    rise = _sun_event(jd0 - 0.2, lon, lat, True)
    # If the event found is the previous day's (search started before midnight), step forward.
    rise_local = engine.jd_to_dt(rise).astimezone(zone)
    if rise_local.date() < local_midnight.date():
        rise = _sun_event(rise + 0.6, lon, lat, True)
        rise_local = engine.jd_to_dt(rise).astimezone(zone)
    if rise_local.date() != local_midnight.date():
        raise ValueError("Could not find sunrise on this civil date")
    sett = _sun_event(rise + 0.05, lon, lat, False)
    sun, moon = _angles(rise)
    elong = (moon - sun) % 360
    ti = tithi_index(elong)
    ni = int(moon // engine.NAK_SPAN) % 27
    yi = yoga_index(sun, moon)
    ki = karana_index(elong)
    k_en, k_hi = karana_names(ki)
    wd = rise_local.weekday()
    part = (sett - rise) / 8
    eighth = RAHU_EIGHTH[wd]
    rk0 = rise + (eighth - 1) * part
    rk1 = rk0 + part

    def ends(pred):
        hit = _until(rise, pred)
        return _iso(hit, zone) if hit else None

    return {
        "date": date,
        "tz": tz,
        "latitude": lat,
        "longitude": lon,
        "vaar": {"en": VAAR_EN[wd], "hi": VAAR_HI[wd], "weekday": wd},
        "sunrise": _iso(rise, zone),
        "sunset": _iso(sett, zone),
        "tithi": {"index": ti + 1, "en": TITHI_EN[ti], "hi": TITHI_HI[ti],
                  "end": ends(lambda jd: tithi_index(_elongation(jd)) == ti)},
        "nakshatra": {"index": ni + 1, "en": engine.NAKSHATRAS[ni], "hi": NAK_HI[ni], "pada": int((moon % engine.NAK_SPAN) // engine.PADA_SPAN) + 1,
                      "end": ends(lambda jd: int(_angles(jd)[1] // engine.NAK_SPAN) % 27 == ni)},
        "yoga": {"index": yi + 1, "en": YOGA_EN[yi], "hi": YOGA_HI[yi],
                 "end": ends(lambda jd: yoga_index(*_angles(jd)) == yi)},
        "karana": {"index": ki + 1, "en": k_en, "hi": k_hi,
                   "end": ends(lambda jd: karana_index(_elongation(jd)) == ki)},
        "rahu_kaal": {"start": _iso(rk0, zone), "end": _iso(rk1, zone), "eighth": eighth},
    }
