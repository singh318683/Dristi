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

SYSTEM_PROMPT = """You are "Acharya", the AI Jyotishi in the {app} app. Classical Parashari Jyotish only: \
sidereal zodiac, Lahiri ayanamsa, whole-sign houses, Vimshottari dasha, gochara. The JSON is the only \
source of truth. Never invent a placement, dignity, aspect, yoga, house lord, period or date.

Timing — follow exactly:
- Three levels, never mixed up: mahadasha, antardasha, pratyantardasha. A pratyantardasha is not an \
antardasha. Do not say "sub-period".
- Quote a period only from vimshottari.mahadasha, vimshottari.antardasha or vimshottari.pratyantardasha. \
Copy status, path, from and to. One name in the path is a mahadasha, two an antardasha, three a \
pratyantardasha. If it is not listed, say the chart does not show that period. Within a list, a \
row's to-date is the next row's from-date.
- Read vimshottari.active_now and vimshottari.level_warning before any timing sentence. status "now" \
is running; "past" is over; "next" is the next one at that same level.
- Write dates as given, day included. Do not move a date into another month.
- For "when", use those rows. A transit date may be quoted only from transits.next_ingress or a Sade \
Sati window. Otherwise say where the planet is today, not when it changes sign.
- Judge a period only after saying how vimshottari.period_lords places that planet (house, dignity, rules).

Reason silently, then answer:
1. Houses for the topic. Career: 10th and its lord, 6th, 2nd, 11th, Sun, Saturn, Mercury, vargas.d10. \
Marriage: 7th and its lord, Venus, Jupiter (also the husband significator when gender is female; Venus \
is the wife significator when gender is male), vargas.d9 lagna and seventh. Money: 2nd, 11th, 5th, 9th, \
Jupiter, listed Dhana yogas. Health: 1st, 6th, 8th, lagna lord, Moon — vitality only. Education: 4th, \
5th, 9th, Mercury, Jupiter. No D24 is in the data. Children: 5th, Jupiter. Property: 4th, Mars, Saturn. \
Foreign: 12th, 9th, 3rd, Rahu.
2. Use only the JSON: occupants, aspects_houses, aspected_by, dignity, retrograde, combust. Rahu and \
Ketu are always retrograde; that is normal. Cite a yoga or dosha only when it is listed.
3. Time with the now and next rows, plus transits from both from_lagna and from_moon. If from_lagna_is \
says Moon, do not call it the ascendant. Sade Sati and dhaiya only as written under doshas.
4. Weigh support against challenge. This is guidance, not fate.

Answer:
- Warm, calm, practical. First name. Same tone every time: no fear, no hype, no guarantees.
- The first 1–2 sentences are the direct answer. Then 3–5 short bullets, each citing one JSON fact. \
Then one date window copied from a dasha row, level named.
- At most one free optional remedy (a mantra, a charity, a habit). Never gemstones, paid rituals, or \
a remedy the person must do.
- Never predict death, lifespan, a disease, an accident, divorce or disaster. Never call anyone \
unlucky or doomed. Difficult periods are phases to move through with care.
- Health or death questions: refuse the medical or longevity prediction in one sentence, speak only \
of general vitality and self-care, and say a doctor is the right person to ask.
- Money: tendencies and timing only. Never a specific investment or a sure gain. Legal questions: \
suggest a lawyer.
- If time_known is false, say so first and follow settings.time_note. Do not state a rising sign.
- Off topic: one line, then offer to return to the chart.
- Under about 220 words unless asked for detail. Simple Markdown. End with one italic line that \
astrology guides and does not decide.
{lang_rule}"""

LANG_RULES = {
    "en": "- Reply in clear, simple English. On first use, gloss lagna (rising sign), mahadasha "
          "(main period), antardasha (period inside the mahadasha) and pratyantardasha (short period "
          "inside the antardasha).",
    "hi": "- पूरा उत्तर सरल स्वाभाविक हिंदी (देवनागरी) में दें। नाम: सूर्य, चंद्र, मंगल, बुध, गुरु, "
          "शुक्र, शनि, राहु, केतु; मेष से मीन। दशा का स्तर कभी न बदलें: महादशा, अंतर्दशा, प्रत्यंतर। "
          "तारीखें JSON जैसी लिखें, दिन सहित। डर या पक्की भविष्यवाणी न लिखें। अस्वीकरण भी हिंदी में, एक पंक्ति।",
}

HOROSCOPE_PROMPT = """You write the daily horoscope for the {app} Vedic app, for Moon sign {sign}. \
Use ONLY the transit JSON; houses are counted from {sign}. Name today's Moon house and one slower \
planet (Jupiter, Saturn or Rahu/Ketu) by house. Do not invent dates, dashas, diseases or fear. \
Give a 2-sentence overview, then one short line each for **Love**, **Career**, **Money**, **Health** \
(self-care only), then "Lucky colour" and "Lucky number". Warm, no certainty, under 140 words. {lang_rule}"""

# Repeated beside the chart JSON so timing rules sit next to the dates they govern.
TIMING_GUARD = ("Timing: quote vimshottari.active_now or a row in vimshottari.mahadasha, "
                "vimshottari.antardasha or vimshottari.pratyantardasha. "
                "A pratyantardasha is not an antardasha. Do not cite a period or date that is not written there.")


def config() -> dict:
    return {"api_key": os.environ.get("LLM_API_KEY", "").strip(),
            "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            "model": os.environ.get("LLM_MODEL", "gpt-4.1-mini")}


def configured() -> bool:
    return bool(config()["api_key"])


def _as_of(chart: dict) -> str:
    return chart["transits"]["as_of_utc"][:10]


def _rules_by_planet(chart: dict) -> dict[str, list[int]]:
    rules = {p: [] for p in chart["planets"]}
    for h in chart["houses"]:
        rules[h["lord"]].append(h["house"])
    return rules


def _period(lord: str, path: str, start: str, end: str) -> dict:
    return {"lord": lord, "path": path, "from": start, "to": end}


def _annotate(rows: list[dict], now_from: str) -> list[dict]:
    """Mark the engine's current row as now. past / next / later follow date order.
    `to` is exclusive: a period ends when the next one starts."""
    rows = sorted(rows, key=lambda r: (r["from"], r["path"]))
    for r in rows:
        if r["from"] == now_from:
            r["status"] = "now"
        elif r["to"] <= now_from:
            r["status"] = "past"
        else:
            r["status"] = "future"
    futures = [r for r in rows if r["status"] == "future"]
    if futures:
        earliest = min(r["from"] for r in futures)
        for r in futures:
            r["status"] = "next" if r["from"] == earliest else "later"
    return [{"status": r["status"], "lord": r["lord"], "path": r["path"], "from": r["from"], "to": r["to"]}
            for r in rows]


def _lord_line(role: str, planet: str, chart: dict, rules: dict[str, list[int]]) -> str:
    r = chart["planets"][planet]
    bits = [f"H{r['house']} {r['sign']}", r["dignity"]]
    if planet in ("Rahu", "Ketu"):
        bits.append("retrograde (normal for nodes)")
    elif r["retrograde"]:
        bits.append("retrograde")
    if r["combust"]:
        bits.append("combust")
    others = [o for o in chart["houses"][r["house"] - 1]["occupants"] if o != planet]
    if others:
        bits.append("with " + " & ".join(others))
    owned = rules.get(planet) or []
    rule = "rules none" if not owned else "rules " + ",".join(f"H{h}" for h in owned)
    aspects = ",".join(str(h) for h in r["aspects_houses"])
    return f"{role} {planet}: {', '.join(bits)}; aspects {aspects}; {rule}"


def _vimshottari(chart: dict) -> dict:
    d = chart["dasha"]
    cur = d.get("current") or {}
    if not cur.get("mahadasha"):
        return {"active_now": "No Vimshottari period contains this date.",
                "mahadasha": [], "antardasha": [], "pratyantardasha": []}
    md, ad, pd = cur["mahadasha"], cur["antardasha"], cur["pratyantardasha"]
    md_rows = [_period(m["lord"], m["lord"], m["start"], m["end"]) for m in d["mahadashas"]]
    block = next(m for m in d["mahadashas"] if m["start"] == md["start"])
    ad_rows = [_period(a["lord"], f"{block['lord']}/{a['lord']}", a["start"], a["end"])
               for a in block["antardashas"]]
    nxt = cur["next_antardasha"]

    def parent_md(start: str) -> str:
        return next(m["lord"] for m in d["mahadashas"] if m["start"] <= start < m["end"])

    if not any(a["from"] == nxt["start"] for a in ad_rows):
        ad_rows.append(_period(nxt["lord"], f"{parent_md(nxt['start'])}/{nxt['lord']}", nxt["start"], nxt["end"]))
    pd_rows = [_period(p["lord"], f"{md['lord']}/{ad['lord']}/{p['lord']}", p["start"], p["end"])
               for p in cur["pratyantardashas"]]
    nxt_parent = parent_md(nxt["start"])
    for p in cur["next_antardasha_pratyantardashas"]:
        pd_rows.append(_period(p["lord"], f"{nxt_parent}/{nxt['lord']}/{p['lord']}", p["start"], p["end"]))
    mds = _annotate(md_rows, md["start"])
    ads = _annotate(ad_rows, ad["start"])
    pds = _annotate(pd_rows, pd["start"])
    now_md = next(r for r in mds if r["status"] == "now")
    now_ad = next(r for r in ads if r["status"] == "now")
    now_pd = next(r for r in pds if r["status"] == "now")
    rules = _rules_by_planet(chart)
    roles = [("mahadasha", now_md["lord"]), ("antardasha", now_ad["lord"]),
             ("pratyantardasha", now_pd["lord"])]
    nxt_pd = next((r for r in pds if r["status"] == "next"), None)
    nxt_ad = next((r for r in ads if r["status"] == "next"), None)
    if nxt_pd:
        roles.append(("next pratyantardasha", nxt_pd["lord"]))
    if nxt_ad:
        roles.append(("next antardasha", nxt_ad["lord"]))
    return {
        "active_now": (f"mahadasha {now_md['path']} ({now_md['from']} to {now_md['to']}); "
                       f"antardasha {now_ad['path']} ({now_ad['from']} to {now_ad['to']}); "
                       f"pratyantardasha {now_pd['path']} ({now_pd['from']} to {now_pd['to']})"),
        "level_warning": (f"{now_pd['path']} ({now_pd['from']} to {now_pd['to']}) is only a pratyantardasha. "
                          f"The antardasha covering those dates is {now_ad['path']} "
                          f"({now_ad['from']} to {now_ad['to']})."),
        "cite": ("Copy status, path, from and to from mahadasha, antardasha or pratyantardasha. "
                 "One name in path = mahadasha, two = antardasha, three = pratyantardasha. "
                 "Within a list, a row's to is the next row's from. Never invent a row or rename the level."),
        "period_lords": [_lord_line(role, planet, chart, rules) for role, planet in roles],
        "mahadasha": mds,
        "antardasha": ads,
        "pratyantardasha": pds,
        "balance_at_birth": d["balance_at_birth"],
    }


def _varga(lagna: str, planets: dict[str, str], reliable: bool, offset: int, focus: str) -> dict:
    idx = engine.SIGNS.index(lagna)
    sign = engine.SIGNS[(idx + offset) % 12]
    return {"lagna": lagna, "lagna_reliable": reliable, "planets": planets,
            f"{focus}_sign": sign, f"{focus}_occupants": [p for p, s in planets.items() if s == sign]}


def _transits(chart: dict) -> dict:
    tr = chart["transits"]
    planets = {}
    for name, v in tr["planets"].items():
        planets[name] = {"sign": v["sign"], "from_lagna": v["house_from_lagna"], "from_moon": v["house_from_moon"],
                         "retrograde": v["retrograde"],
                         "dignity": engine.dignity(name, engine.SIGNS.index(v["sign"]), v["degree"])}
    ref = "natal Lagna" if chart["time_known"] else "natal Moon (birth time unknown; this is not the ascendant)"
    return {"as_of": tr["as_of_utc"][:10], "dates": "UTC calendar dates", "from_lagna_is": ref,
            "planets": planets,
            "next_ingress": engine.next_ingresses(datetime.fromisoformat(tr["as_of_utc"]))}


def _doshas(chart: dict) -> list[dict]:
    out = []
    for d in chart["doshas"]:
        if d["name"].startswith("Manglik"):
            out.append({"name": "Manglik (Kuja) Dosha", "level": d["level"],
                        "from": d.get("from") or [], "mitigations": d.get("mitigations") or []})
        elif d["name"].startswith("Kaal"):
            item = {"name": "Kaal Sarp Dosha", "present": d["present"]}
            if d.get("type"):
                item["type"] = d["type"]
            if d.get("basis"):
                item["basis"] = d["basis"]
            out.append(item)
        elif d["name"].startswith("Sade"):
            item = {"name": "Sade Sati", "active": d["active"], "saturn_sign": d["saturn_transit_sign"],
                    "house_from_moon": d["saturn_house_from_moon"]}
            if d.get("phase"):
                item["phase"] = d["phase"]
            if d.get("small_panoti_dhaiya"):
                item["dhaiya"] = d["small_panoti_dhaiya"]
            for key in ("current_window", "next_window"):
                w = d.get(key)
                if not w:
                    continue
                item[key] = {"from": w.get("start") or "before the searched range", "to": w["end"]}
            out.append(item)
    return out


def _time_note(chart: dict) -> str:
    lords = engine.moon_nakshatra_lords_that_day(datetime.fromisoformat(chart["birth_utc"]))
    if len(set(lords)) > 1:
        return ("Birth time unknown. Houses and D9/D10 lagnas are counted from the Moon, not the ascendant. "
                "The Moon changed nakshatra that day, so the Vimshottari lord is uncertain: give themes, "
                "not exact period dates, and say a birth time would settle it.")
    return ("Birth time unknown. Houses and D9/D10 lagnas are counted from the Moon, not the ascendant. "
            "The Moon stayed in one nakshatra that day, so the dasha lord is usable, but dates are "
            "approximate. Do not state a rising sign.")


def compact_chart(profile: dict, chart: dict) -> dict:
    """Chart JSON for the model: closed dasha lists, both transit references, D9 and D10."""
    rules = _rules_by_planet(chart)
    planets = {}
    for name, r in chart["planets"].items():
        planets[name] = {"sign": r["sign"], "degree": round(r["degree"], 2), "nakshatra": r["nakshatra"],
                         "pada": r["pada"], "house": r["house"], "dignity": r["dignity"],
                         "retrograde": r["retrograde"], "combust": r["combust"],
                         "rules": rules[name], "aspects_houses": r["aspects_houses"]}
    houses = [{"house": h["house"], "sign": h["sign"], "lord": h["lord"], "lord_in_house": h["lord_in_house"],
               "lord_in_sign": h["lord_in_sign"], "lord_dignity": chart["planets"][h["lord"]]["dignity"],
               "occupants": h["occupants"], "aspected_by": h["aspected_by"]} for h in chart["houses"]]
    d9p = {p: chart["planets"][p]["navamsa_sign"] for p in engine.PLANETS}
    d10p = {p: chart["planets"][p]["dasamsa_sign"] for p in engine.PLANETS}
    known = chart["time_known"]
    settings = {"zodiac": "sidereal", "ayanamsa": "Lahiri", "houses": "whole sign", "time_known": known,
                "house_reference": chart["house_reference"],
                "nodes": "Rahu and Ketu are always retrograde; that is normal, not an extra affliction."}
    if not known:
        settings["time_note"] = _time_note(chart)
    lagna = chart["lagna"]
    yogas = []
    for y in chart["yogas"]:
        item = {"name": y["name"], "basis": y.get("basis", "")}
        if y.get("strength") and y["strength"] not in ("normal", "strong"):
            item["strength"] = y["strength"]
        yogas.append(item)
    return {
        "as_of": _as_of(chart),
        "person": {"name": profile.get("name"), "gender": profile.get("gender") or "unspecified",
                   "birth_date": profile.get("date"), "birth_time": profile.get("time") or "unknown",
                   "birth_place": profile.get("place")},
        "settings": settings,
        "lagna": {"sign": lagna["sign"], "degree": round(lagna["degree"], 2), "nakshatra": lagna["nakshatra"],
                  "pada": lagna["pada"], "reliable": lagna["reliable"]},
        "moon_sign": chart["moon_sign"],
        "janma_nakshatra": f'{chart["janma_nakshatra"]} pada {chart["janma_pada"]}',
        "planets": planets,
        "houses": houses,
        "vargas": {"d9": {"use": "marriage, dharma, planet strength",
                          **_varga(chart["d9"]["lagna_sign"], d9p, known, 6, "seventh")},
                   "d10": {"use": "career, status, karma",
                           **_varga(chart["d10"]["lagna_sign"], d10p, known, 9, "tenth")}},
        "yogas": yogas,
        "doshas": _doshas(chart),
        "vimshottari": _vimshottari(chart),
        "transits": _transits(chart),
    }


def build_chat_messages(app_name: str, profile: dict, chart: dict, history: list[dict], lang: str) -> list[dict]:
    """System prompt plus chart JSON, then the last 10 user/assistant turns."""
    lang = lang if lang in LANG_RULES else "en"
    ctx = compact_chart(profile, chart)
    system = SYSTEM_PROMPT.format(app=app_name, lang_rule=LANG_RULES[lang])
    chart_msg = (f"Today is {ctx['as_of']} UTC. {TIMING_GUARD}\n"
                 + json.dumps(ctx, ensure_ascii=False, separators=(",", ":")))
    msgs = [{"role": "system", "content": system}, {"role": "system", "content": chart_msg}]
    for m in (history or [])[-10:]:
        if m.get("role") in ("user", "assistant"):
            msgs.append({"role": m["role"], "content": (m.get("content") or "")[:4000]})
    return msgs


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
