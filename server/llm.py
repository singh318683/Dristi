"""LLM layer: OpenAI-compatible /chat/completions (or native Anthropic Messages API when
LLM_BASE_URL is https://api.anthropic.com/v1), configured by env vars.

LLM_API_KEY   required to enable AI (never hardcoded)
LLM_BASE_URL  default https://api.openai.com/v1
LLM_MODEL     default gpt-4.1-mini
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import AsyncIterator

import httpx

import engine

SYSTEM_PROMPT = """You are "Acharya", the AI Jyotishi inside the {app} app. You practise classical \
Vedic astrology (Parashari): sidereal zodiac, Lahiri ayanamsa, whole-sign houses, Vimshottari dasha, gochara.

The JSON was computed by Swiss Ephemeris. It is the only chart. Never invent a placement, house lord, \
aspect, yoga, dignity, combustion flag, or date. If it is not in the JSON, say you cannot see it. \
Do not recompute lordship (use rules_houses), aspects, dignity, combustion, or dasha dates.

Timing (strict):
- Name a period only from vimshottari.cite_only. Copy lord, start, and end, and say which level the row is \
(mahadasha, antardasha, or pratyantardasha — the key or active.level, not a guess).
- Levels: mahadasha (main period), antardasha (inside the mahadasha), pratyantardasha (a short slice \
inside the antardasha). Do not say "sub-period" alone. Read cite_only.do_not_collapse and keep that nesting: \
a pratyantardasha does not end its antardasha.
- Antardashas are listed only for the active mahadasha. Pratyantardashas only for the active antardasha \
and the next one. For any other mahadasha, give its start and end only.
- Time a job change, marriage, move, or money decision with an antardasha or mahadasha window. \
You may mention the active pratyantardasha only with its exact dates and its parent antardasha. \
Do not tell the person to put off a major decision until a pratyantardasha ends.
- Transit sign changes: only gochar.ingresses (they include retrograde returns). Sade Sati and Dhaiya: \
only the Sade Sati dosha. No other dates, and no station dates. Do not calculate a transit date yourself.
- The only rising sign is lagna.sign. An earlier message is not a chart. If it names another lagna or \
another period, ignore it and use this JSON.

How to read the question (silently, then answer):
- Career or a job: houses 10, 6, 2, 11, their lords, Sun, Saturn, Mercury, and D10.
- Marriage: house 7, its lord, Venus, Jupiter (more weight when gender is female), and D9.
- Money: houses 2, 11, 5, 9, Jupiter. Education: houses 4, 5, 9, Mercury, Jupiter.
- Children: house 5, Jupiter. Property: house 4, Mars, Saturn. Foreign: houses 12, 9, Rahu.
- Health: houses 1 and 6, the lagna lord, and the Moon. Vitality and self-care only.
- For those houses use sign, occupants, aspected_by, the lord's house and lord_dignity, retrograde, \
combust, and the listed yogas. Read gochar from_lagna and from_moon (Saturn, Jupiter, Rahu, Ketu). \
Manglik: quote that dosha's label only. sources are the only references that cause it. A reference named in not_from does not. \
D9 checks marriage and planet strength; D10 checks career. If divisional_lagnas.reliable is false, \
do not quote a D9 or D10 lagna (planet d9 and d10 signs are still valid).

How to answer:
- Warm, plain, brief. Use the person's first name. No fear, no fate. Never "cursed", "doomed", or "unlucky". \
Say "this period favours" or "this period asks for care", not "this will happen".
- First sentence: the direct answer. If they asked when, put one copied window in that sentence.
- Then 3–5 short bullets. Each cites one JSON fact, such as the 10th lord's sign, house, and dignity, \
or an antardasha with its start and end copied from cite_only and the mahadasha it sits inside.
- At most two date windows, both copied from cite_only or gochar.ingresses. Quote YYYY-MM-DD.
- One optional gentle remedy (a mantra, a charity, a fast, or a habit). It is free and never required. No gemstones, no paid puja, and do not push a purchase.
- After the disclaimer, a heading on its own line, exactly "In simple words" in an English reply or \
"सरल शब्दों में" in a Hindi reply. Then 2 to 4 sentences for someone with no astrology background: \
what this means in daily life and one thing to do. Do not use dasha, mahadasha, antardasha, \
pratyantardasha, lagna, house numbers, or Sanskrit words. No fear and no guarantees.
- The very last line, after that plain section, must be exactly: FACTORS: houses <numbers>; planets <names>; dasha <lord> mahadasha, <lord> antardasha
  Name only houses, planets, and the dasha lords you actually used. No dates on that line.
- Death, lifespan, accidents, surgery, or a named disease: refuse that prediction in one sentence. Then only \
general vitality and self-care, and tell them to see a doctor. Do not use the 8th house or maraka planets.
- Other health: self-care only, plus a doctor. Money: tendencies, never a specific investment or a guaranteed gain. \
Law: suggest a lawyer.
- If time_known is false: houses are from the Moon (Chandra lagna). Say that, and do not quote a rising sign.
- Off-topic: one short reply, then invite a chart question.
- Under 200 words unless they ask for more. Simple Markdown (bold, bullets).
- End with the one-line italic disclaimer from the language rule.
{lang_rule}"""

LANG_RULES = {
    "en": "- Reply in clear, simple English. On first use, gloss lagna (rising sign), mahadasha (main period), "
          "antardasha (period inside it), pratyantardasha (short period inside the antardasha). "
          "Disclaimer: \"_Astrology offers guidance, not certainty; please use your own judgement._\"",
    "hi": "- पूरा उत्तर सरल हिंदी (देवनागरी) में दें। सूर्य, चंद्र, मंगल, बुध, गुरु, शुक्र, शनि, राहु, केतु; "
          "मेष से मीन; भाव संख्या के साथ (दशम भाव)। दशा के स्तर केवल: महादशा, अंतर्दशा, प्रत्यंतर। "
          "अंग्रेज़ी वाक्य या अकेला \"sub-period\" नहीं। तारीख JSON जैसी (YYYY-MM-DD), नई तारीख नहीं। "
          "अंत में: \"_ज्योतिष मार्गदर्शन है, निश्चित भविष्यवाणी नहीं; अपना विवेक रखें।_\"",
}

HOROSCOPE_PROMPT = """You are the daily-horoscope writer for the {app} Vedic astrology app. \
Write today's horoscope only for people whose Moon sign (rashi) is {sign} ({sign_hi}). \
Use ONLY the sidereal transit JSON. Every house_from_rashi value is already counted from {sign}; \
use those numbers and do not recount from another rashi or from the Moon's current sign. \
The first line must name this rashi ({sign} in English, {sign_hi} in Hindi) and the date in the JSON. \
Do not title the reading with any other sign, even when a planet is transiting that sign. \
Mention the Moon's house_from_rashi and one slower influence (Jupiter, Saturn or Rahu/Ketu) by its house_from_rashi. \
Give: a 2-sentence overview, then one short line each for **Love**, **Career**, **Money**, **Health** \
(self-care only, no medical claims), then "Lucky colour" and "Lucky number". \
Warm and encouraging, no fear, no certainty, under 140 words. {lang_rule}"""


def config() -> dict:
    return {"api_key": os.environ.get("LLM_API_KEY", "").strip(),
            "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            "model": os.environ.get("LLM_MODEL", "gpt-4.1-mini")}


def configured() -> bool:
    return bool(config()["api_key"])


def _span(level: str, p: dict, **extra) -> dict:
    row = {"level": level, "lord": p["lord"], "start": p["start"], "end": p["end"]}
    row.update(extra)
    return row


def _rows(periods: list, current_start: str | None = None) -> list:
    """lord/start/end only. The enclosing key (or `inside`) is the level, so it is not repeated."""
    rows = []
    for p in periods:
        row = {"lord": p["lord"], "start": p["start"], "end": p["end"]}
        if current_start and p["start"] == current_start:
            row["now"] = True
        rows.append(row)
    return rows


def cite_only(chart: dict) -> dict:
    """The only dasha periods the model is allowed to name, with levels that cannot be collapsed."""
    d = chart["dasha"]
    cur = d.get("current") or {}
    as_of = chart["transits"]["as_of_utc"][:10]
    mahadashas = _rows(d["mahadashas"], (cur.get("mahadasha") or {}).get("start"))
    rule = ("Cite only these rows. Copy lord, start, and end. end is the first day of the next period. "
            "mahadashas, antardashas, and pratyantardashas are different levels: a pratyantardasha is inside "
            "its antardasha and does not end it. Antardashas are listed only for the active mahadasha; "
            "pratyantardashas only for the active antardasha and the next one. Do not invent a lord, a level, or a date.")
    if not cur.get("mahadasha"):
        return {"as_of": as_of, "rule": rule + " No period covers this date.",
                "active": None, "now_line": None, "mahadashas": mahadashas}
    md, ad, pd = cur["mahadasha"], cur["antardasha"], cur["pratyantardasha"]
    ads = next(m["antardashas"] for m in d["mahadashas"] if m["start"] == md["start"])
    pds = cur["pratyantardashas"]
    nxt, nxt_md = cur.get("next_antardasha"), cur.get("next_antardasha_mahadasha")
    nxt_pds = cur.get("next_antardasha_pratyantardashas") or []
    pd_i = next(i for i, p in enumerate(pds) if p["start"] == pd["start"])
    if pd_i + 1 < len(pds):
        n = pds[pd_i + 1]
        next_pd = (f"Next pratyantardasha: {n['lord']} {n['start']} to {n['end']}, "
                   f"still inside {ad['lord']} antardasha (ends {ad['end']}).")
    else:
        next_pd = f"{pd['lord']} pratyantardasha ends the {ad['lord']} antardasha on {ad['end']}."
    next_ad = ""
    if nxt:
        next_ad = (f" Next antardasha: {nxt['lord']} {nxt['start']} to {nxt['end']}, "
                   f"inside {nxt_md} mahadasha.")
    return {
        "as_of": as_of,
        "rule": rule,
        "now_line": (f"{md['lord']} mahadasha ({md['start']} to {md['end']}) > "
                     f"{ad['lord']} antardasha ({ad['start']} to {ad['end']}) > "
                     f"{pd['lord']} pratyantardasha ({pd['start']} to {pd['end']})"),
        "do_not_collapse": (
            f"The active short period is {pd['lord']} pratyantardasha ({pd['start']} to {pd['end']}) "
            f"inside {ad['lord']} antardasha (until {ad['end']}) inside {md['lord']} mahadasha (until {md['end']}). "
            f"Ending the pratyantardasha does not end the antardasha. "
            f"Do not describe this {pd['lord']} pratyantardasha as the antardasha or the mahadasha."),
        "next_line": next_pd + next_ad,
        "active": {
            "mahadasha": _span("mahadasha", md),
            "antardasha": _span("antardasha", ad, mahadasha=md["lord"]),
            "pratyantardasha": _span("pratyantardasha", pd, antardasha=ad["lord"], mahadasha=md["lord"]),
        },
        "mahadashas": mahadashas,
        "antardashas": {"inside": f"{md['lord']} mahadasha", "rows": _rows(ads, ad["start"])},
        "pratyantardashas": {"inside": f"{ad['lord']} antardasha, {md['lord']} mahadasha",
                             "rows": _rows(pds, pd["start"])},
        "next_antardasha_pratyantardashas": {
            "inside": f"{nxt['lord']} antardasha, {nxt_md} mahadasha" if nxt else None,
            "rows": _rows(nxt_pds) if nxt else [],
        },
    }


def _gochar(chart: dict) -> dict:
    tr = chart["transits"]
    known = chart["time_known"]
    planets = {}
    for name, v in tr["planets"].items():
        row = {"sign": v["sign"], "deg": round(v["degree"], 1), "from_moon": v["house_from_moon"],
               "retrograde": v["retrograde"]}
        if known:
            row["from_lagna"] = v["house_from_lagna"]
        planets[name] = row
    return {
        "as_of": tr["as_of_utc"][:10],
        "note": ("Read both from_lagna (from the ascendant) and from_moon (from the natal Moon)."
                 if known else
                 "Birth time unknown: there is no from_lagna. Use from_moon only."),
        "planets": planets,
        "ingresses": {"rule": "Only these sign changes in the next 2 years, including retrograde returns. Ketu stays opposite Rahu. Do not invent a station or any other date.",
                      "events": engine.slow_ingresses(datetime.fromisoformat(tr["as_of_utc"]), 2.0)},
    }


def chart_facts(ctx: dict) -> str:
    """The lagna, current periods, and ingresses the model must copy. Same chart as the chart tab and the PDF."""
    lagna = ctx.get("lagna") or {}
    rise = f"Lagna {lagna['sign']}" if lagna.get("reliable") and lagna.get("sign") else "Birth time unknown; do not name a rising sign"
    cite = (ctx.get("vimshottari") or {}).get("cite_only") or {}
    periods = cite.get("now_line") or "none"
    events = ((ctx.get("gochar") or {}).get("ingresses") or {}).get("events") or []
    ing = "; ".join(f"{e['planet']} enters {e['enters']} on {e['date']}" for e in events if e.get("date"))
    return (f"This chart only. {rise}. Current periods, copy exactly: {periods}. "
            f"Transit dates you may state, copy exactly and state no others: {ing or 'none'}.")


def compact_chart(profile: dict, chart: dict) -> dict:
    """Prompt JSON. Dasha timing is an explicit cite list so the model cannot invent periods."""
    known = chart["time_known"]
    rules = {p: [] for p in chart["planets"]}
    for h in chart["houses"]:
        rules[h["lord"]].append(h["house"])
    planets = {}
    for p, r in chart["planets"].items():
        planets[p] = {
            "sign": r["sign"], "deg": round(r["degree"], 2), "nakshatra": r["nakshatra"], "pada": r["pada"],
            "house": r["house"], "rules_houses": rules[p], "dignity": r["dignity"],
            "retrograde": r["retrograde"], "combust": r["combust"],
            "aspects_houses": r["aspects_houses"], "d9": r["navamsa_sign"], "d10": r["dashamsha_sign"],
        }
    houses = [{
        "house": h["house"], "sign": h["sign"], "lord": h["lord"],
        "lord_in_house": h["lord_in_house"], "lord_in_sign": h["lord_in_sign"],
        "lord_dignity": chart["planets"][h["lord"]]["dignity"],
        "occupants": h["occupants"], "aspected_by": h["aspected_by"],
    } for h in chart["houses"]]
    L = chart["lagna"]
    if known:
        lagna = {"reliable": True, "sign": L["sign"], "deg": round(L["degree"], 2),
                 "nakshatra": L["nakshatra"], "pada": L["pada"], "d9": L["navamsa_sign"], "d10": L["dashamsha_sign"]}
        division = {"reliable": True, "d9_lagna": L["navamsa_sign"], "d10_lagna": L["dashamsha_sign"],
                    "use": "D9 (navamsa) for marriage and planet strength; D10 (dashamsha) for career. Planet signs are d9 and d10 on each planet."}
    else:
        lagna = {"reliable": False, "note": "Do not cite a rising sign. Birth time is unknown."}
        division = {"reliable": False,
                    "use": "Planet d9 and d10 signs are valid. D9 and D10 lagnas are not, because birth time is unknown."}
    cite = cite_only(chart)
    cur = chart["dasha"].get("current") or {}
    # `current` kept so existing readers still find the active mahadasha lord.
    # Parents are repeated here so this short block cannot be read as a second timeline.
    if cur.get("mahadasha"):
        md, ad, pd = cur["mahadasha"], cur["antardasha"], cur["pratyantardasha"]
        current = {
            "mahadasha": {"lord": md["lord"], "start": md["start"], "end": md["end"]},
            "antardasha": {"lord": ad["lord"], "start": ad["start"], "end": ad["end"], "mahadasha": md["lord"]},
            "pratyantardasha": {"lord": pd["lord"], "start": pd["start"], "end": pd["end"],
                                "antardasha": ad["lord"], "mahadasha": md["lord"]},
        }
        if cur.get("next_antardasha"):
            nxt = cur["next_antardasha"]
            current["next_antardasha"] = {"lord": nxt["lord"], "start": nxt["start"], "end": nxt["end"],
                                          "mahadasha": cur.get("next_antardasha_mahadasha")}
    else:
        current = {}
    out = {
        "as_of": cite["as_of"],
        "person": {"name": profile.get("name"), "gender": profile.get("gender") or "unspecified",
                   "birth_date": profile.get("date"), "birth_time": profile.get("time") or "unknown",
                   "birth_place": profile.get("place")},
        "settings": {
            "zodiac": "sidereal", "ayanamsa": "Lahiri", "houses": "whole sign",
            "time_known": known,
            "house_reference": "Lagna" if known else "Moon (Chandra lagna)",
            "lordship": "rules_houses is classical sign lordship. Dignity is not lordship.",
            "nodes": "Rahu and Ketu are mean nodes, always retrograde (normal, not an extra affliction) and rule no house.",
            "aspects": "aspects_houses and aspected_by are whole-sign graha drishti (Mars 4/7/8, Jupiter and Rahu/Ketu 5/7/9, Saturn 3/7/10, others 7). Do not recompute.",
        },
        "lagna": lagna,
        "moon_sign": chart["moon_sign"],
        "janma_nakshatra": f'{chart["janma_nakshatra"]} pada {chart["janma_pada"]}',
        "vimshottari": {"current": current, "cite_only": cite},
        "gochar": _gochar(chart),
        "planets": planets,
        "houses": houses,
        "divisional_lagnas": division,
        "yogas": chart["yogas"],
        "doshas": chart["doshas"],
    }
    if not known:
        out["time_warning"] = ("Birth time unknown. Houses are Chandra lagna, counted from the Moon. "
                               "Do not quote a rising sign or a D9/D10 lagna. Precision is lower.")
    return out


async def stream_chat(messages: list[dict], max_tokens: int = 900) -> AsyncIterator[str]:
    c = config()
    got = False
    if "anthropic.com" in c["base_url"]:
        gen = _stream_anthropic(c, messages, max_tokens)
    else:
        gen = _stream_openai(c, messages, max_tokens)
    async for tok in gen:
        got = True
        yield tok
    if not got:
        # Never "succeed" with an empty answer (the user would be charged for nothing)
        raise RuntimeError("LLM returned no text")


async def _stream_openai(c: dict, messages: list[dict], max_tokens: int) -> AsyncIterator[str]:
    body = {"model": c["model"], "messages": messages, "stream": True}
    m = c["model"].lower()
    if m.startswith(("gpt-5", "o1", "o3", "o4")) and "chat" not in m:
        # OpenAI reasoning models: no temperature, budget includes hidden reasoning tokens
        body["max_completion_tokens"] = max_tokens * 4
        body["reasoning_effort"] = os.environ.get("LLM_REASONING_EFFORT", "low")
    else:
        body["temperature"] = 0.6
        body["max_tokens"] = max_tokens
    headers = {"Authorization": f"Bearer {c['api_key']}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
        async with client.stream("POST", f"{c['base_url']}/chat/completions", json=body, headers=headers) as r:
            if r.status_code >= 400:
                txt = (await r.aread()).decode(errors="ignore")[:300]
                raise RuntimeError(f"LLM HTTP {r.status_code}: {txt}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
                if delta:
                    yield delta


async def _stream_anthropic(c: dict, messages: list[dict], max_tokens: int) -> AsyncIterator[str]:
    """Native Anthropic Messages API (used when LLM_BASE_URL points at api.anthropic.com)."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns: list[dict] = []
    for m in messages:
        if m["role"] not in ("user", "assistant") or not m.get("content"):
            continue
        if turns and turns[-1]["role"] == m["role"]:
            turns[-1]["content"] += "\n\n" + m["content"]  # merge back-to-back turns
        else:
            turns.append({"role": m["role"], "content": m["content"]})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    while turns and turns[-1]["role"] != "user":  # newer Claude models reject a trailing assistant turn
        turns.pop()
    # Newer Claude models think (adaptively) by default and thinking counts toward max_tokens,
    # so give room for it and keep effort low: these are short chart readings.
    body = {"model": c["model"], "max_tokens": max_tokens * 4, "system": system,
            "messages": turns, "stream": True,
            "output_config": {"effort": os.environ.get("LLM_EFFORT", "low")}}
    headers = {"x-api-key": c["api_key"], "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    base = c["base_url"][:-3] if c["base_url"].endswith("/v1") else c["base_url"]
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
        async with client.stream("POST", f"{base}/v1/messages", json=body, headers=headers) as r:
            if r.status_code >= 400:
                txt = (await r.aread()).decode(errors="ignore")[:300]
                raise RuntimeError(f"LLM HTTP {r.status_code}: {txt}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                t = ev.get("type")
                if t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    if ev["delta"].get("text"):
                        yield ev["delta"]["text"]
                elif t == "message_delta" and ev.get("delta", {}).get("stop_reason"):
                    stop = ev["delta"]["stop_reason"]
                    if stop not in ("end_turn", "stop_sequence"):
                        raise RuntimeError(f"LLM stopped early ({stop})")
                elif t == "error":
                    raise RuntimeError(f"LLM stream error: {json.dumps(ev.get('error'))[:200]}")
                elif t == "message_stop":
                    break


async def complete(messages: list[dict], max_tokens: int = 500) -> str:
    return "".join([t async for t in stream_chat(messages, max_tokens)])
