"""LLM layer: OpenAI-compatible /chat/completions, configured by env vars.

LLM_API_KEY   required to enable AI (never hardcoded)
LLM_BASE_URL  default https://api.openai.com/v1
LLM_MODEL     default gpt-4.1-mini
"""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

SYSTEM_PROMPT = """You are "Acharya", the AI Jyotishi inside the {app} app. You practise classical \
Vedic astrology (Parashari Jyotish): sidereal zodiac, Lahiri ayanamsa, whole-sign houses, \
Vimshottari dasha and gochara (transits).

You are given a JSON object computed by a Swiss Ephemeris engine. Treat it as the ONLY source of \
truth about the chart. Never invent placements, degrees, yogas or dates that are not in the JSON; \
if something is not there, say you cannot see it.

How to reason (do this silently, then answer):
1. Identify the houses that govern the question (e.g. career: 10th, its lord, 6th/2nd/11th, \
Sun/Saturn/Mercury; marriage: 7th, its lord, Venus (and Jupiter for women by tradition), D9 navamsa; \
finance: 2nd, 11th, 5th, 9th, Jupiter; health: 1st, 6th, 8th, lagna lord, Moon; \
education: 4th, 5th, 9th, Mercury, Jupiter; children: 5th, Jupiter; property: 4th, Mars, Saturn; \
foreign travel: 12th, 9th, 3rd, Rahu).
2. Judge those houses: sign, occupants, aspects (graha drishti), the house lord's placement and dignity \
(exalted/own/moolatrikona/friendly/neutral/enemy/debilitated), retrograde/combust status, and any listed yogas/doshas.
3. Time it: the current mahadasha / antardasha lords, which houses they rule and occupy in this chart, \
and the current transits (especially Saturn, Jupiter, Rahu/Ketu from Lagna and from Moon, Sade Sati).
4. Weigh supportive against challenging factors and give a balanced conclusion.

How to answer:
- Warm, respectful, conversational, like a wise family astrologer. Address the person by first name.
- Start with a 1–2 sentence direct answer, then 3–6 short bullet points of reasoning. Each bullet must \
cite the specific placement it relies on, e.g. "10th lord Saturn in the 4th, in Taurus (friendly sign)" or \
"Mars–Venus antardasha (until Dec 2027)".
- When useful, name a time window from the dasha/transit dates in the JSON (month + year).
- Offer 1–2 gentle, optional traditional remedies (mantra, charity, a day of fasting, behaviour) only if relevant. \
Never push gemstones, paid pujas or anything costly, and never say a remedy is required.
- Never frighten. Frame difficult periods as phases to navigate with effort and care. \
Do not predict death, serious illness, accidents, divorce or disasters, and do not label anyone as unlucky.
- Health: speak only in terms of general vitality and self-care; always suggest seeing a doctor for medical concerns. \
Money/investments: describe tendencies and timing, never specific investments or guaranteed returns. \
Legal matters: suggest a qualified lawyer. Never present astrology as certainty.
- If time of birth is unknown (time_known=false), say houses are read from the Moon (Chandra lagna) and \
precision is lower.
- If the question is not about astrology or the person's life, answer briefly and steer back.
- Keep it under about 220 words unless the user asks for detail. Use simple Markdown (bold, bullets).
- End with a one-line italic disclaimer, e.g. "_Astrology offers guidance, not certainty; please use your own judgement._"
{lang_rule}"""

LANG_RULES = {
    "en": "- Reply in clear, simple English. Keep Sanskrit terms (lagna, dasha, bhava) with a short gloss.",
    "hi": "- पूरा उत्तर सरल हिंदी (देवनागरी) में दें। ग्रहों, राशियों और भावों के नाम हिंदी में लिखें "
          "(जैसे सूर्य, चंद्र, मंगल, बुध, गुरु, शुक्र, शनि, राहु, केतु; मेष…मीन; दशम भाव)। "
          "Disclaimer भी हिंदी में दें।",
}

HOROSCOPE_PROMPT = """You are the daily-horoscope writer for the {app} Vedic astrology app. \
Write today's horoscope for people with Moon sign (rashi) {sign}, based ONLY on the sidereal transit \
JSON given (houses are counted from {sign}). Mention the Moon's transit house today and one slower \
influence (Jupiter, Saturn or Rahu/Ketu) by house. Give: a 2-sentence overview, then one short line each for \
**Love**, **Career**, **Money**, **Health** (self-care only, no medical claims), then "Lucky colour" and \
"Lucky number". Warm and encouraging, no fear, no certainty, under 140 words. {lang_rule}"""


def config() -> dict:
    return {"api_key": os.environ.get("LLM_API_KEY", "").strip(),
            "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            "model": os.environ.get("LLM_MODEL", "gpt-4.1-mini")}


def configured() -> bool:
    return bool(config()["api_key"])


def compact_chart(profile: dict, chart: dict) -> dict:
    """Smaller JSON for the prompt (keeps everything the astrologer needs)."""
    d = chart["dasha"]
    cur_md = d["current"].get("mahadasha", {})
    timeline = [{"lord": m["lord"], "start": m["start"], "end": m["end"]} for m in d["mahadashas"]]
    ads = next((m["antardashas"] for m in d["mahadashas"] if m["start"] == cur_md.get("start")), [])
    planets = {p: {k: v for k, v in r.items() if k in (
        "sign", "degree", "nakshatra", "pada", "house", "dignity", "retrograde", "combust", "navamsa_sign", "aspects_houses")}
        for p, r in chart["planets"].items()}
    return {
        "person": {"name": profile.get("name"), "gender": profile.get("gender") or "unspecified",
                   "birth_date": profile.get("date"), "birth_time": profile.get("time") or "unknown",
                   "birth_place": profile.get("place")},
        "settings": {"zodiac": "sidereal", "ayanamsa": chart["ayanamsa"], "houses": "whole sign",
                     "time_known": chart["time_known"], "house_reference": chart["house_reference"]},
        "lagna": {k: chart["lagna"][k] for k in ("sign", "degree", "nakshatra", "pada", "navamsa_sign")},
        "moon_sign": chart["moon_sign"], "janma_nakshatra": f'{chart["janma_nakshatra"]} pada {chart["janma_pada"]}',
        "planets": planets,
        "houses": [{k: h[k] for k in ("house", "sign", "lord", "lord_in_house", "occupants", "aspected_by")} for h in chart["houses"]],
        "navamsa_d9": chart["d9"],
        "yogas": chart["yogas"], "doshas": chart["doshas"],
        "vimshottari": {"current": d["current"], "mahadasha_timeline": timeline,
                        "antardashas_in_current_mahadasha": ads},
        "transits_now": chart["transits"],
    }


async def stream_chat(messages: list[dict], max_tokens: int = 900) -> AsyncIterator[str]:
    c = config()
    body = {"model": c["model"], "messages": messages, "stream": True}
    m = c["model"].lower()
    if m.startswith(("gpt-5", "o1", "o3", "o4")) and "chat" not in m:
        # OpenAI reasoning models: no temperature, budget includes hidden reasoning tokens
        body["max_completion_tokens"] = max_tokens * 4
        body["reasoning_effort"] = os.environ.get("LLM_REASONING_EFFORT", "low")
    else:
        if not m.startswith("claude"):
            # newer Claude models reject `temperature`; other providers still accept it
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


async def complete(messages: list[dict], max_tokens: int = 500) -> str:
    return "".join([t async for t in stream_chat(messages, max_tokens)])
