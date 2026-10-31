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
Other env: `DRISHTI_DB` (sqlite path, used when `DATABASE_URL` is unset), `LLM_REASONING_EFFORT` (for gpt-5/o-series). Acharya questions are not configured with a free-question count or a credit price. See below.

### Persistence, referrals, sign-in

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | no | Postgres URL (`postgres://` or `postgresql://`). Neon: use the **pooled** string (host contains `-pooler`). SSL is added when the URL does not set `sslmode`. When set, wallets, sign-in, birth profiles and Acharya chats are stored there and survive redeploys. When unset, SQLite at `DRISHTI_DB` is used (default `server/data/app.db`; on Vercel, `/tmp/app.db`, which is wiped on each cold start). The phone still keeps a local copy either way. |
| `DRISHTI_DB` | no | SQLite file. Ignored when `DATABASE_URL` is set. |
| `APP_PUBLIC_URL` | only to send mail | Public origin used inside the magic-link email, e.g. `https://your-app.example`. |
| `SMTP_HOST` | no | If unset, email magic links are stored but not sent (the API does not return the token). |
| `SMTP_PORT` | no | Default `587` (STARTTLS). |
| `SMTP_USER` / `SMTP_PASSWORD` | with SMTP | SMTP login. Leave unset for an open relay on port 25. |
| `SMTP_FROM` | no | From address. Falls back to `SMTP_USER`. |
| `PLAY_SERVICE_ACCOUNT_JSON` | to answer questions | Google Play service-account key, as JSON or base64. Without it every Acharya question is refused. Do not commit it. |
| `PLAY_SERVICE_ACCOUNT_FILE` | local only | Path to that key file. Ignored when `PLAY_SERVICE_ACCOUNT_JSON` is set. |
| `PLAY_PACKAGE_NAME` | no | Default `com.ikshana.drishti`. Must match the Play app. |
| `PLAY_PRODUCT_ID` | no | Default `acharya_question`. Must match the consumable. |

No secret belongs in source. `POST /api/wallet/topup` and `POST /api/wallet/order` return 410. They do not add credits.

Sign-in is optional. `POST /api/auth/magic` creates a one-time email link. `POST /api/auth/otp` is a stub (`OtpProvider`) until an SMS vendor is chosen. Signing in records the email on this device. Birth profiles and Acharya chats saved on that phone move to the account the first time, so another signed-in device can load them from `GET /api/sync`. A question is paid on the device that asks it.

A referral code is `GET /api/referral?device=`. The new device calls `POST /api/referral/attach` with `{device, code}` (the web app does this from `/?ref=CODE`). The link does not include a question.

## Engine notes
* Time zones: IANA tzdb history (DST, India war time 1942–45 = UTC+6:30). If tzdb only has LMT, or India before 1906,
  true local mean time from the birth longitude is used. A manual UTC-offset override exists per profile.
* Unknown birth time: chart cast for 12:00 local, houses counted from the Moon (Chandra lagna), lagna flagged unreliable.
* Dasha year = 365.25 days. Nodes = mean. Rahu exalted in Taurus / Ketu in Scorpio.
* Rebuild cities: download `cities15000.zip`, `admin1CodesASCII.txt`, `countryInfo.txt` from download.geonames.org into
  `server/data/` (as `admin1.txt`, `countries.txt`) and run `python server/build_cities.py`.

## Acharya questions are $0.99

Each question to Acharya is one Google Play consumable: product id `acharya_question`, price **$0.99** in the United States. There is no free question and no credit pack. The birth chart, life periods, dosha, Sade Sati, panchang, matching, daily horoscope and kundli PDF stay free and do not ask for a purchase.

The phone starts a Play purchase, then `POST /api/chat` sends the purchase token with the question. The server asks Google whether that token is a completed purchase of `acharya_question` for package `com.ikshana.drishti`. A cancelled, pending, promo, or rewarded purchase is refused. One token pays for one question. The server consumes the purchase only after the answer has started, so a failure before any answer leaves the same purchase usable. A second question needs a new $0.99 purchase.

Until `PLAY_SERVICE_ACCOUNT_JSON` is set, the server refuses every question. It does not answer for free.

In the Android app, either:

* install Drishti as a Trusted Web Activity, so the page can use the Play Billing Digital Goods API (`https://play.google.com/billing`), or
* wrap the page in a WebView and set `window.DrishtiPlay` before the page loads:

```javascript
window.DrishtiPlay = {
  // Resolve {purchaseToken} for "acharya_question". Reject if the person cancels.
  // Do not consume the purchase here. The server consumes it after the answer starts.
  purchase(productId) {},
  // Resolve [{itemId, purchaseToken}] for purchases Google has not consumed yet.
  listPurchases() {},
};
```

A desktop browser cannot open Play Billing. The page says so, and it does not send the question.

### Play Console setup

1. In Play Console, create the app with package name `com.ikshana.drishti` if it is not there already.
2. Open Monetize → Products → One-time products. (Older consoles call these in-app products.)
3. Create a product with id `acharya_question`. The id cannot be changed later. Name it "Acharya question" and describe it as one question to the AI astrologer.
4. If the form asks consumable or non-consumable, choose consumable. If it only offers a one-time product, that is the right type: the server consumes it after a successful answer, which lets the same person buy another.
5. Set the United States price to $0.99. Do not add a pack, a subscription, or a cheaper first question. Activate the product.
6. In Google Cloud, enable the Google Play Android Developer API. Create a service account and a JSON key. Do not put that file in git.
7. In Play Console → Setup → API access, link that Cloud project. Then Users and permissions → invite the service-account email, and grant it access to view financial data and manage orders, so it can read and consume purchases.
8. On the host that serves the API (production is `https://dristi-six.vercel.app`), set `PLAY_SERVICE_ACCOUNT_JSON` to the whole JSON key and redeploy. Optional overrides are `PLAY_PACKAGE_NAME` and `PLAY_PRODUCT_ID`.
9. Add your own Google account under License testing and install the app from an internal testing track. Those test purchases are accepted. They are not a free question for everyone else.
10. Buy one question, confirm an answer, then ask a second question and confirm Play asks for $0.99 again.

## Stubbed
* Phone OTP: interface only (`OtpProvider`). Email magic link works without a provider key, and sends mail only when `SMTP_HOST` is set.
* Birth profiles and Acharya chats are stored for the account once `DATABASE_URL` is set.
* Web app only (no native Android project in this repo). Play Billing is reached through the Digital Goods API or `window.DrishtiPlay`.

## Now, in the app
Single-viewport screens (detail opens in a sheet). Acharya is labelled an AI astrologer. The $0.99 price is shown before a question is sent. Answers can expand a "Why" line of houses, planets and dasha. Dosha, Sade Sati and panchang cards are free. Kundli PDF is `POST /api/kundli.pdf?lang=en|hi`. Share cards are drawn in the browser (1080×1920) and use the Web Share API, with a download fallback.
