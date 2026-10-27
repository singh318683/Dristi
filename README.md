# Drishti (दृष्टि) · AI Vedic astrologer

Astrotalk-style app where the "astrologer" is an LLM that reads a real, computed Vedic birth chart.
Ikshana Solutions · planned bundle id `com.ikshana.drishti`.

```
web/      single-file mobile web app (index.html), privacy.html, manifest, icons, sw.js
          (same pattern as Tithi, so the iOS WKWebView / Android WebView wrappers can be reused)
server/   FastAPI backend
  engine.py    Swiss Ephemeris chart engine (sidereal Lahiri, whole-sign, mean node)
  panchang.py  tithi, nakshatra, yoga, karana, sunrise/sunset, Rahu kaal
  matching.py  Ashtakoota 36-guna milan
  llm.py       system prompt + OpenAI-compatible streaming client
  db.py        SQLite, or Postgres when DATABASE_URL is set
  pdfkundli.py free kundli PDF (English / Hindi)
  app.py       API: cities, chart, panchang, match, chat (SSE), horoscope, wallet, referral, auth, kundli.pdf
  fonts/       Noto Sans Devanagari (OFL) for the PDF
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
Other env: `FREE_QUESTIONS` (3), `QUESTION_COST` (15 credits), `DRISHTI_DB` (sqlite path, used when `DATABASE_URL` is unset), `LLM_REASONING_EFFORT` (for gpt-5/o-series).

### Persistence, referrals, sign-in

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | no | Postgres URL (`postgres://` or `postgresql://`, for example Vercel Postgres or Neon). When set, wallets, referrals and sign-in links are stored there. When unset, SQLite at `DRISHTI_DB` is used (default `server/data/app.db`; on Vercel, `/tmp/app.db`). |
| `DRISHTI_DB` | no | SQLite file. Ignored when `DATABASE_URL` is set. |
| `REFERRAL_BONUS` | no | Credits given to both the inviter and the new device when that device asks its first question. Default `15`. |
| `APP_PUBLIC_URL` | only to send mail | Public origin used inside the magic-link email, e.g. `https://your-app.example`. |
| `SMTP_HOST` | no | If unset, email magic links are stored but not sent (the API does not return the token). |
| `SMTP_PORT` | no | Default `587` (STARTTLS). |
| `SMTP_USER` / `SMTP_PASSWORD` | with SMTP | SMTP login. Leave unset for an open relay on port 25. |
| `SMTP_FROM` | no | From address. Falls back to `SMTP_USER`. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | no | Reserved for a later Razorpay integration. `POST /api/wallet/order` stays a 501 until order creation is implemented. Do not put these values in the repo. |

No secret belongs in source. `POST /api/wallet/topup` still adds demo credits and does not charge a card or UPI.

Sign-in is optional. `POST /api/auth/magic` creates a one-time email link. `POST /api/auth/otp` is a stub (`OtpProvider`) until an SMS vendor is chosen. Signing in records the email on this device; it does not yet merge wallets across devices.

A referral code is `GET /api/referral?device=`. The new device calls `POST /api/referral/attach` with `{device, code}` (the web app does this from `/?ref=CODE`). The bonus is paid inside `charge()`, when that device's first question is actually asked.

## Engine notes
* Time zones: IANA tzdb history (DST, India war time 1942–45 = UTC+6:30). If tzdb only has LMT, or India before 1906,
  true local mean time from the birth longitude is used. A manual UTC-offset override exists per profile.
* Unknown birth time: chart cast for 12:00 local, houses counted from the Moon (Chandra lagna), lagna flagged unreliable.
* Dasha year = 365.25 days. Nodes = mean. Rahu exalted in Taurus / Ketu in Scorpio.
* Rebuild cities: download `cities15000.zip`, `admin1CodesASCII.txt`, `countryInfo.txt` from download.geonames.org into
  `server/data/` (as `admin1.txt`, `countries.txt`) and run `python server/build_cities.py`.

## Stubbed
* Payments: demo top-up only. `POST /api/wallet/order` is the Razorpay seam and returns 501. No UPI charge is made.
* Phone OTP: interface only (`OtpProvider`). Email magic link works without a provider key, and sends mail only when `SMTP_HOST` is set.
* Cross-device wallet merge after sign-in is not implemented; credits stay on the device id.
* Web app only (no native builds yet).

## Now, in the app
Single-viewport screens (detail opens in a sheet). Acharya is labelled an AI astrologer; the credit cost is shown before a question is sent; answers can expand a "Why" line of houses, planets and dasha. Dosha, Sade Sati and panchang cards are free. Kundli PDF is `POST /api/kundli.pdf?lang=en|hi`. Share cards are drawn in the browser (1080×1920) and use the Web Share API, with a download fallback.
