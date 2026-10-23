# Drishti (दृष्टि) · AI Vedic astrologer

Astrotalk-style app where the "astrologer" is an LLM that reads a real, computed Vedic birth chart.
Ikshana Solutions · planned bundle id `com.ikshana.drishti`.

```
web/      single-file mobile web app (index.html), privacy.html, manifest, icons, sw.js
          (same pattern as Tithi, so the iOS WKWebView / Android WebView wrappers can be reused)
server/   FastAPI backend
  engine.py    Swiss Ephemeris chart engine (sidereal Lahiri, whole-sign, mean node)
  matching.py  Ashtakoota 36-guna milan
  llm.py       system prompt + OpenAI-compatible streaming client
  app.py       API: /api/cities /api/offset /api/chart /api/match /api/chat (SSE) /api/horoscope /api/wallet
  ephe/        Swiss Ephemeris data files (sepl_18, semo_18, seas_18: 1800–2400 CE)
  data/cities.tsv  34k cities (GeoNames cities15000, CC-BY 4.0) with IANA time zones
tests/    pytest suite (reference charts vs AstroSage), compare_refs.py, mock_llm.py
tools/shots.js  puppeteer screenshots -> shots/
```

## Run
```bash
uv venv --python 3.12 .venv && . .venv/bin/activate && uv pip install -r requirements.txt   # needs gcc/g++
export LLM_API_KEY=...            # optional; without it the app runs and chat says "AI not configured yet"
export LLM_BASE_URL=https://api.openai.com/v1   # any OpenAI-compatible endpoint
export LLM_MODEL=gpt-4.1-mini
./run.sh                          # http://localhost:8792  (PORT=... to change)
python -m pytest -q tests && python tests/compare_refs.py
```
Other env: `FREE_QUESTIONS` (3), `QUESTION_COST` (15 credits), `DRISHTI_DB` (sqlite path), `LLM_REASONING_EFFORT` (for gpt-5/o-series).

## Engine notes
* Time zones: IANA tzdb history (DST, India war time 1942–45 = UTC+6:30). If tzdb only has LMT, or India before 1906,
  true local mean time from the birth longitude is used. A manual UTC-offset override exists per profile.
* Unknown birth time: chart cast for 12:00 local, houses counted from the Moon (Chandra lagna), lagna flagged unreliable.
* Dasha year = 365.25 days. Nodes = mean. Rahu exalted in Taurus / Ketu in Scorpio.
* Rebuild cities: download `cities15000.zip`, `admin1CodesASCII.txt`, `countryInfo.txt` from download.geonames.org into
  `server/data/` (as `admin1.txt`, `countries.txt`) and run `python server/build_cities.py`.

## Stubbed
Payments (wallet top-up adds demo credits), no user accounts (anonymous device id), web app only (no native builds yet).
