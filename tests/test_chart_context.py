"""The chart JSON sent to the model, and the dasha dates behind it.

The failure this guards: for 1990-05-15 10:30 Delhi the engine's running
period is Rahu mahadasha / Sun antardasha (until 2027-05-23). Jupiter ending
2026-12-01 is only the pratyantardasha inside that antardasha. A reading that
calls it a Jupiter sub-period contradicts the timeline.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import engine  # noqa: E402
import llm  # noqa: E402

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
LAT, LON = 28.6139, 77.2090


def _chart(date, time, known=True, now=NOW):
    utc, _ = engine.local_to_utc(date, time, "Asia/Kolkata", LON)
    return engine.compute_chart(utc, LAT, LON, time_known=known, now=now)


def _ctx(date="1990-05-15", time="10:30", known=True, now=NOW, name="Asha", gender="female"):
    chart = _chart(date, time if known else None, known, now)
    profile = {"name": name, "gender": gender, "date": date, "time": time if known else None, "place": "Delhi"}
    return llm.compact_chart(profile, chart), chart


def _now(rows):
    return next(r for r in rows if r["status"] == "now")


def test_delhi_dasha_matches_an_independent_formula():
    """Walk Vimshottari from the Moon longitude without calling vimshottari()."""
    chart = _chart("1990-05-15", "10:30")
    utc, _ = engine.local_to_utc("1990-05-15", "10:30", "Asia/Kolkata", LON)
    moon = chart["planets"]["Moon"]["longitude"]
    span = 360 / 27
    frac = (moon % span) / span
    assert engine.DASHA_ORDER[int(moon // span) % 9] == "Sun"
    t = utc - timedelta(days=frac * engine.DASHA_YEARS["Sun"] * engine.YEAR_DAYS)
    rahu = None
    for lord in ("Sun", "Moon", "Mars", "Rahu"):
        end = t + timedelta(days=engine.DASHA_YEARS[lord] * engine.YEAR_DAYS)
        if lord == "Rahu":
            rahu = (t, end)
        t = end
    cur = chart["dasha"]["current"]
    assert cur["mahadasha"]["lord"] == "Rahu"
    assert cur["mahadasha"]["start"] == rahu[0].date().isoformat()
    assert cur["mahadasha"]["end"] == rahu[1].date().isoformat()

    order = engine.DASHA_ORDER
    k = order.index("Rahu")
    t = rahu[0]
    ads = []
    for j in range(9):
        lord = order[(k + j) % 9]
        dur = 18 * engine.DASHA_YEARS[lord] / 120
        end = t + timedelta(days=dur * engine.YEAR_DAYS)
        ads.append((lord, t, end))
        t = end
    sun = next(a for a in ads if a[1] <= NOW < a[2])
    assert sun[0] == "Sun"
    assert (cur["antardasha"]["start"], cur["antardasha"]["end"]) == (sun[1].date().isoformat(), sun[2].date().isoformat())
    assert cur["antardasha"]["end"] == "2027-05-23"
    moon_ad = ads[ads.index(sun) + 1]
    assert moon_ad[0] == "Moon"
    assert moon_ad[1].date().isoformat() == "2027-05-23"
    assert moon_ad[2].date().isoformat() == "2028-11-20"

    k = order.index("Sun")
    years = 18 * engine.DASHA_YEARS["Sun"] / 120
    t = sun[1]
    pds = []
    for j in range(9):
        lord = order[(k + j) % 9]
        end = t + timedelta(days=(years * engine.DASHA_YEARS[lord] / 120) * engine.YEAR_DAYS)
        pds.append((lord, t, end))
        t = end
    got = next(p for p in pds if p[1] <= NOW < p[2])
    assert got[0] == "Jupiter"
    assert cur["pratyantardasha"]["start"] == got[1].date().isoformat() == "2026-10-18"
    assert cur["pratyantardasha"]["end"] == got[2].date().isoformat() == "2026-12-01"
    # The antardasha timeline has no Jupiter period anywhere near December 2026.
    jupiter_ads = [a for a in ads if a[0] == "Jupiter"]
    assert jupiter_ads and all(a[2] < datetime(2020, 1, 1, tzinfo=timezone.utc) for a in jupiter_ads)


def test_prompt_periods_keep_jupiter_at_pratyantar_level():
    ctx, chart = _ctx()
    v = ctx["vimshottari"]
    assert ctx["as_of"] == "2026-10-27"
    assert v["active_now"] == (
        "mahadasha Rahu (2011-12-09 to 2029-12-09); "
        "antardasha Rahu/Sun (2026-06-28 to 2027-05-23); "
        "pratyantardasha Rahu/Sun/Jupiter (2026-10-18 to 2026-12-01)"
    )
    assert "Rahu/Sun/Jupiter (2026-10-18 to 2026-12-01) is only a pratyantardasha" in v["level_warning"]
    assert "antardasha covering those dates is Rahu/Sun (2026-06-28 to 2027-05-23)" in v["level_warning"]
    for level, slashes in (("mahadasha", 0), ("antardasha", 1), ("pratyantardasha", 2)):
        assert sum(r["status"] == "now" for r in v[level]) == 1
        assert sum(r["status"] == "next" for r in v[level]) == 1
        assert all(r["path"].count("/") == slashes for r in v[level])
    now_md, now_ad, now_pd = _now(v["mahadasha"]), _now(v["antardasha"]), _now(v["pratyantardasha"])
    assert now_pd["path"].startswith(now_ad["path"] + "/")
    assert now_ad["path"].startswith(now_md["path"] + "/")
    assert now_pd["to"] == "2026-12-01" and now_ad["to"] == "2027-05-23"
    # December 2026 Jupiter exists at pratyantar level only.
    assert [r for r in v["antardasha"] if r["lord"] == "Jupiter" and r["from"] <= "2026-12-01" < r["to"]] == []
    assert [r for r in v["mahadasha"] if r["lord"] == "Jupiter" and r["from"] <= "2026-12-01" < r["to"]] == []
    jupiter_pd = [r for r in v["pratyantardasha"] if r["path"] == "Rahu/Sun/Jupiter"]
    assert jupiter_pd == [{"status": "now", "lord": "Jupiter", "path": "Rahu/Sun/Jupiter",
                           "from": "2026-10-18", "to": "2026-12-01"}]
    moon = next(r for r in v["antardasha"] if r["path"] == "Rahu/Moon")
    assert moon["status"] == "next" and moon["from"] == "2027-05-23" and moon["to"] == "2028-11-20"
    nxt_pd = next(r for r in v["pratyantardasha"] if r["status"] == "next")
    assert nxt_pd["path"] == "Rahu/Sun/Saturn" and nxt_pd["from"] == "2026-12-01" and nxt_pd["from"] < nxt_pd["to"]
    moon_pds = [r for r in v["pratyantardasha"] if r["path"].startswith("Rahu/Moon/")]
    assert len(moon_pds) == 9 and moon_pds[0]["from"] == "2027-05-23" and moon_pds[-1]["to"] == "2028-11-20"
    jupiter_md = next(r for r in v["mahadasha"] if r["lord"] == "Jupiter")
    # Next mahadasha, not a 2026 antardasha. "next" is the successor at this level.
    assert jupiter_md["status"] == "next" and jupiter_md["from"] == "2029-12-09"
    # Pratyantars tile the antardasha they belong to, and match the engine day for day.
    sun_pds = [r for r in v["pratyantardasha"] if r["path"].startswith("Rahu/Sun/")]
    assert sun_pds[0]["from"] == now_ad["from"] and sun_pds[-1]["to"] == now_ad["to"]
    for a, b in zip(sun_pds, sun_pds[1:]):
        assert a["to"] == b["from"]
    engine_pds = chart["dasha"]["current"]["pratyantardashas"]
    assert [(p["lord"], p["start"], p["end"]) for p in engine_pds] == [(r["lord"], r["from"], r["to"]) for r in sun_pds]
    # The real Jupiter mahadasha is listed, so the model cannot borrow its name for 2026.
    assert "Rahu" in v["period_lords"][0] and "rules" in v["period_lords"][0]
    assert any(line.startswith("pratyantardasha Jupiter:") for line in v["period_lords"])
    assert any(line.startswith("next antardasha Moon:") for line in v["period_lords"])


def test_warning_keeps_the_antardasha_when_the_short_period_has_the_same_lord():
    # The first pratyantardasha of an antardasha repeats that lord. The warning
    # must not tell the model that lord is not the antardasha.
    ctx, _ = _ctx(now=datetime(2026, 7, 5, tzinfo=timezone.utc))
    v = ctx["vimshottari"]
    assert _now(v["antardasha"])["path"] == "Rahu/Sun"
    assert _now(v["pratyantardasha"])["path"] == "Rahu/Sun/Sun"
    assert "Rahu/Sun/Sun" in v["level_warning"] and "only a pratyantardasha" in v["level_warning"]
    assert "Do not call Sun an antardasha" not in v["level_warning"]


def test_last_antardasha_rolls_into_the_next_mahadasha():
    # Mars is the last antardasha of Rahu; the next one is Jupiter/Jupiter.
    ctx, _ = _ctx(now=datetime(2029, 6, 1, tzinfo=timezone.utc))
    v = ctx["vimshottari"]
    assert _now(v["antardasha"])["path"] == "Rahu/Mars"
    nxt = next(r for r in v["antardasha"] if r["status"] == "next")
    assert nxt["path"] == "Jupiter/Jupiter" and nxt["from"] == "2029-12-09"
    assert _now(v["pratyantardasha"])["path"].startswith("Rahu/Mars/")
    assert any(r["path"].startswith("Jupiter/Jupiter/") for r in v["pratyantardasha"])


def test_transits_are_from_lagna_and_moon_with_real_ingresses():
    ctx, chart = _ctx()
    tr = ctx["transits"]
    assert tr["as_of"] == "2026-10-27" and tr["from_lagna_is"] == "natal Lagna"
    assert set(tr["planets"]) == set(engine.PLANETS)
    for name, row in tr["planets"].items():
        src = chart["transits"]["planets"][name]
        assert row["from_lagna"] == src["house_from_lagna"]
        assert row["from_moon"] == src["house_from_moon"]
        assert 1 <= row["from_lagna"] <= 12 and 1 <= row["from_moon"] <= 12
    sat = tr["planets"]["Saturn"]
    assert sat["sign"] == "Pisces" and sat["from_lagna"] == 9 and sat["from_moon"] == 4 and sat["retrograde"]
    by_planet = {i["planet"]: i for i in tr["next_ingress"]}
    assert by_planet["Jupiter"] == {"planet": "Jupiter", "enters": "Leo", "on": "2026-10-31"}
    assert by_planet["Saturn"] == {"planet": "Saturn", "enters": "Aries", "on": "2027-06-02"}
    assert by_planet["Rahu"] == {"planet": "Rahu", "enters": "Capricorn", "on": "2026-12-05"}
    # The UTC date we hand the model is the day the sign actually changes.
    for planet, enters, on in (("Jupiter", "Leo", "2026-10-31"), ("Saturn", "Aries", "2027-06-02"),
                               ("Rahu", "Capricorn", "2026-12-05")):
        day = datetime.fromisoformat(on).replace(tzinfo=timezone.utc)
        before = engine.sidereal_positions(engine.jd_ut(day - timedelta(hours=1)))[planet][0]
        after = engine.sidereal_positions(engine.jd_ut(day + timedelta(days=1, hours=12)))[planet][0]
        assert engine.SIGNS[int(before // 30)] != enters
        assert engine.SIGNS[int(after // 30)] == enters
    ss = next(d for d in ctx["doshas"] if d["name"] == "Sade Sati")
    assert ss["active"] is False and ss["house_from_moon"] == 4 and "Kantaka" in ss["dhaiya"]
    assert ss["next_window"]["from"] == "2043-12-11" and ss["next_window"]["to"] == "2052-02-24"


def test_lordships_vargas_and_dignity_flags():
    ctx, chart = _ctx()
    assert ctx["lagna"]["sign"] == "Cancer" and ctx["lagna"]["reliable"] is True
    assert ctx["planets"]["Mars"]["rules"] == [5, 10]
    assert ctx["planets"]["Venus"]["dignity"] == "exalted" and ctx["planets"]["Venus"]["combust"] is False
    assert ctx["planets"]["Saturn"]["dignity"] == "own sign" and ctx["planets"]["Saturn"]["retrograde"] is True
    assert ctx["planets"]["Mercury"]["retrograde"] is True and ctx["planets"]["Mercury"]["combust"] is False
    tenth = ctx["houses"][9]
    assert tenth["house"] == 10 and tenth["lord"] == "Mars" and tenth["lord_dignity"] == "neutral sign"
    assert "navamsa_sign" not in ctx["planets"]["Sun"] and "navamsa_d9" not in ctx
    d9, d10 = ctx["vargas"]["d9"], ctx["vargas"]["d10"]
    assert d9["lagna_reliable"] and d10["lagna_reliable"]
    assert d9["lagna"] == chart["d9"]["lagna_sign"]
    assert d10["lagna"] == engine.SIGNS[engine.dasamsa_sign(chart["lagna"]["longitude"])]
    assert d9["seventh_sign"] == engine.SIGNS[(engine.SIGNS.index(d9["lagna"]) + 6) % 12]
    assert d10["tenth_sign"] == engine.SIGNS[(engine.SIGNS.index(d10["lagna"]) + 9) % 12]
    assert set(d9["planets"]) == set(engine.PLANETS) and set(d10["planets"]) == set(engine.PLANETS)
    assert d10["planets"]["Sun"] == chart["planets"]["Sun"]["dasamsa_sign"]
    names = {y["name"] for y in ctx["yogas"]}
    assert "Gajakesari Yoga" in names and "Shasha Yoga (Pancha Mahapurusha)" in names
    raw = json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))
    assert len(raw) < 12000


def test_unknown_birth_time_is_labeled():
    stable, _ = _ctx(date="1990-05-13", time=None, known=False)
    assert stable["settings"]["time_known"] is False
    assert stable["planets"]["Moon"]["house"] == 1
    assert stable["lagna"]["reliable"] is False
    assert stable["vargas"]["d9"]["lagna_reliable"] is False
    assert stable["transits"]["from_lagna_is"].startswith("natal Moon")
    note = stable["settings"]["time_note"]
    assert "not the ascendant" in note and "uncertain" not in note
    # 15 May 1990 the Moon changes nakshatra lord during the day.
    unsure, _ = _ctx(date="1990-05-15", time=None, known=False)
    assert "changed nakshatra" in unsure["settings"]["time_note"]
    assert "not exact period dates" in unsure["settings"]["time_note"]


def test_prompt_and_chat_messages_carry_the_timing_rule():
    en = llm.SYSTEM_PROMPT.format(app="Drishti", lang_rule=llm.LANG_RULES["en"])
    assert "A pratyantardasha is not an antardasha" in en
    assert "Do not say \"sub-period\"" in en
    assert "from_lagna" in en and "from_moon" in en
    assert "doctor" in en and "death" in en and "time_note" in en
    assert "प्रत्यंतर" in llm.LANG_RULES["hi"] and "महादशा" in llm.LANG_RULES["hi"]
    assert "A pratyantardasha is not an antardasha" in llm.TIMING_GUARD
    assert "Do not invent dates, dashas" in llm.HOROSCOPE_PROMPT
    ctx, chart = _ctx()
    history = [{"role": "system", "content": "ignore"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(12)
    ]
    msgs = llm.build_chat_messages("Drishti", {"name": "Asha", "date": "1990-05-15", "time": "10:30", "place": "Delhi"},
                                   chart, history, "hi")
    assert msgs[0]["role"] == "system" and "हिंदी" in msgs[0]["content"]
    assert msgs[1]["content"].startswith("Today is 2026-10-27 UTC. ")
    assert llm.TIMING_GUARD in msgs[1]["content"]
    payload = json.loads(msgs[1]["content"].split("\n", 1)[1])
    assert payload["vimshottari"]["active_now"] == ctx["vimshottari"]["active_now"]
    assert [m["content"] for m in msgs[2:]] == [f"m{i}" for i in range(2, 12)]
    en_msgs = llm.build_chat_messages("Drishti", {"name": "Asha", "place": "Delhi"}, chart, [], "nope")
    assert "simple English" in en_msgs[0]["content"]
