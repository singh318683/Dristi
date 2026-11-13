# Personal astrologers

Chat and voice with a person, beside the $0.99 Acharya question. The website and the Android app stay as they are until this is switched on.

## Switch

| Variable | Default | Purpose |
|---|---|---|
| `ASTROLOGER_CONNECT_ENABLED` | off | `1` shows the connect sheet and opens the API. Anything else keeps today's app. |
| `DATABASE_URL` | unset | Postgres. Neon: use the **pooled** string (host contains `-pooler`). SSL is added when the URL does not set `sslmode`. Without it the flag cannot serve a wallet, and existing charts, Acharya, and Play billing keep working. |
| `ASTROLOGER_CONNECT_ALLOW_SQLITE` | off | Test-only. Do not set this on Vercel. |
| `ADMIN_TOKEN` | unset | Bearer token for `/api/connect/admin/`. Neetu approves astrologers and sets each per-minute rate here. The rate is stored in the database. The phone cannot send a price. |
| `APPLE_COMMISSION_RATE` | `0.15` | Applied only to the part of a session paid from App Store wallet credit. Astrologer 75% and platform 25% are taken from what remains. |

Vercel Postgres or Neon: create the database, copy the pooled URL into `DATABASE_URL`, then set `ASTROLOGER_CONNECT_ENABLED=1`. Tables are created on first use. If the URL is missing, `/api/status` reports `astrologer_connect: false` and the new routes answer `503` instead of charging anyone.

## Money

Wallet products are `wallet_5`, `wallet_10`, and `wallet_20` (500, 1000, and 2000 cents). A session places a five-minute hold at the astrologer's stored rate, the same rate for chat and call. The server clock starts when the astrologer accepts. Heartbeats from both sides, and a LiveKit webhook for calls, end the session. The charge is the elapsed seconds times the rate, floored to the cent, and never more than the hold. The unused hold returns to the same wallet bucket. Stripe-funded cents are split in full. App Store cents are reduced by `APPLE_COMMISSION_RATE` first. Ledger lines for one transaction always balance: user wallet, hold, astrologer payable, platform revenue, and the Apple fee.

Top-ups:

- Web, including Apple Pay: `POST /api/connect/wallet/intent` creates a Stripe PaymentIntent for the product. The webhook `POST /api/connect/stripe/webhook` credits the wallet once. Set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and `STRIPE_PUBLISHABLE_KEY`.
- iOS: the app sends the StoreKit 2 transaction id. The server calls the App Store Server API. Set `APPLE_ISSUER_ID`, `APPLE_KEY_ID`, `APPLE_IAP_PRIVATE_KEY`, `APPLE_BUNDLE_ID` (`com.ikshana.drishti`), and `APPLE_IAP_ENV=sandbox` for sandbox. `PyJWT` and `cryptography` sign that request.

`acharya_question` stays $0.99. On iOS the WebView bridge `window.DrishtiStore.purchase` can buy it, and `/api/chat` with `store: "apple"` verifies that transaction. Android still uses Google Play. A missing or reused purchase is not answered.

## Chat and voice

Chat is polled (`/messages` and `/heartbeat` every few seconds). That works on Vercel's short functions. Pusher or Ably can replace the poll later without changing the ledger.

Voice is [LiveKit](https://livekit.io). Set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`. The server mints a ten-minute join token only after the hold exists and the astrologer has accepted a call. No phone numbers are exchanged. Point LiveKit's webhook at `POST /api/connect/livekit/webhook`. The `Authorization` header is a short JWT signed with `LIVEKIT_API_SECRET` whose `sha256` claim is the body. `participant_left` and `room_finished` end the session. Mute and speaker stay on the device. The iOS wrapper adds the microphone permission and CallKit.

## Payouts

Astrologers onboard with Stripe Connect Express (`POST /api/connect/astro/stripe`, needs `APP_PUBLIC_URL`). Earnings sit for 7 days. Monday at 12:00 UTC, Vercel cron calls `GET /api/cron/connect-payouts` with `Authorization: Bearer $CRON_SECRET` and sends Stripe transfers for payable balances of at least $1. The cron is in `vercel.json`.

Razorpay Route is the later India payout option. It is not wired. The ledger account ids stay the same when it is added.

## People

Astrologers apply at `POST /api/connect/apply` and sign in at `/#astro`. They toggle online, accept a chat or call, and read earnings. Neetu uses `/#admin` with `ADMIN_TOKEN` to approve and set the cents-per-minute rate.

A user can report or block an astrologer from the chat. Blocked astrologers disappear from that user's list.

The offer sheet appears when an Acharya answer is closed, and only when the flag is on. Maybe later dismisses that sheet. The next answer can offer it again.
