# Personal astrologers

The iPhone app is a separate product: the same Drishti as the website and Android app, plus live astrologers. It is a second Vercel project, for example `drishti-ios`, deploying **only** from `feature/astrologer-connect` (and later its release branch) with `ASTROLOGER_CONNECT_ENABLED=1`. The production project that serves https://dristi-six.vercel.app stays on `main` with the flag unset until the Android release below is approved, so Android and the current site do not change.

The iOS shell does not load that production site. GitHub Actions writes the repository variable `IOS_SERVER_URL` into `ios/capacitor.config.json` before generating the Xcode project. Leave it unset and the shell uses `https://drishti-ios.vercel.app`. Set it to the separate Vercel project deployed from this branch. See [ios/README.md](../ios/README.md).

## Switch

| Variable | Default | Purpose |
|---|---|---|
| `ASTROLOGER_CONNECT_ENABLED` | off | `1` shows registration, the connect sheet, and the API. |
| `DATABASE_URL` | unset | Postgres. Neon: the **pooled** URL (host contains `-pooler`). Without it the flag cannot open a wallet and the rest of the app keeps working. |
| `ASTROLOGER_CONNECT_PLATFORMS` | unset (every client) | Comma list of `ios`, `android`, `web`. When set, only those clients see the feature. Apps are recognised by the `X-Drishti-Shell` header. Webhooks, cron and `/#admin` are never limited. Leave unset on `drishti-ios` (today's behaviour). Use `ios,android` on `dristi` so the public website does not show it. |
| `ANDROID_GOOGLE_PAY_ENABLED` | off | `1` offers Google Pay inside the Stripe wallet sheet, in the Android app only. Turn on only after Google Pay is enabled in the Stripe dashboard. |
| `CONNECT_LOCK_ENDED_CHATS` | off | Android always locks a chat after it ends. `1` locks it for every client. |
| `ASTROLOGER_CONNECT_ALLOW_SQLITE` | off | Tests only. Do not set this on Vercel. |
| `ADMIN_TOKEN` | unset | Bearer token for `/#admin`. Approve, suspend, remove, or reset a password. A reset shows a temporary password once. The admin does not set the rate. |
| `ASTRO_RATE_MIN` | `0.30` | Lowest per-minute rate. A decimal is dollars. A whole number is cents (`30`). |
| `ASTRO_RATE_MAX` | `5.00` | Highest per-minute rate. Same parsing. Default ceiling is $5.00 (`500` cents). |
| `PLATFORM_COMMISSION_RATE` | `0.05` | Drishti's share of each session's gross. The provider keeps the rest (95%). |
| `PROVIDER_TRIAL_DAYS` | `90` | One free trial. No lifetime plan. |
| `PROVIDER_GRACE_DAYS` | `3` | After `invoice.payment_failed`, the provider stays bookable this many days, then disappears. |
| `PROVIDER_MONTHLY_LABEL` | `$9.99` | Label only. Stripe charges the price id. |
| `PROVIDER_YEARLY_LABEL` | `$99` | Label only. |
| `STRIPE_PRICE_PROVIDER_MONTHLY` | unset | Stripe Billing price id for the monthly plan. |
| `STRIPE_PRICE_PROVIDER_YEARLY` | unset | Stripe Billing price id for the yearly plan. |
| `STRIPE_SECRET_KEY` | unset | PaymentIntents, subscriptions, Connect, and the customer portal. |
| `STRIPE_WEBHOOK_SECRET` | unset | Signs `POST /api/connect/stripe/webhook`. The endpoint in Stripe must include `payment_intent.succeeded`. |
| `STRIPE_PUBLISHABLE_KEY` | unset | Wallet top-up in the browser, including Apple Pay. |
| `APP_PUBLIC_URL` | unset | Success and return URLs for Checkout, the portal, and Connect. Wallet 3DS returns to `{APP_PUBLIC_URL}/#wallet`. |
| `CRON_SECRET` | unset | `Authorization: Bearer` for the Monday payout cron. |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | unset | Voice rooms. Tokens are minted only after a hold and an accepted call. |

Customer payments for a live session are Stripe card payments. Apple Pay is not offered in the wallet sheet today (`applePay: 'never'`). On iOS they are person-to-person services under App Store guideline 3.1.3(d), not in-app purchases. StoreKit remains only for the $0.99 Acharya question. See `ios/APP_REVIEW.md` for the review risk. On Android they are a 1:1 online paid service under Google Play's Payments policy, and Play billing remains only for the $0.99 Acharya question. See [android/PLAY_REVIEW_NOTES.md](../android/PLAY_REVIEW_NOTES.md).

## Who can be booked

A provider sets one rate, from the minimum through the maximum, for both chat and call. Neetu approves, suspends, or removes them. They appear online only when they are approved, toggled online, and either inside the trial, on an active subscription, or inside the grace period after a failed payment.

Stripe webhooks update that state: `customer.subscription.created`, `updated`, and `deleted`, and `invoice.payment_failed`.

## Money

Wallet packs `wallet_5`, `wallet_10`, and `wallet_20` are Stripe PaymentIntents. After the browser confirms a payment, the server reads that PaymentIntent from Stripe and credits it. The Stripe webhook must include `payment_intent.succeeded` and uses the same payment id, so a retry does not credit the wallet twice. A session holds five minutes at the provider's stored rate. The server clock starts when they accept, not while the request is still ringing. If nobody accepts within 2 minutes, the hold is returned in full. Heartbeats and the LiveKit webhook end the session. The charge is elapsed seconds times the rate, floored to the cent, and never more than the hold. Unused hold returns to the wallet.

Of the gross, Drishti takes `PLATFORM_COMMISSION_RATE` and the provider the remainder. Twelve minutes at $1.00 is $12.00 gross, $0.60 Drishti, $11.40 provider. Amounts are integer cents and the two shares sum to the gross. There is no 75/25 split and no App Store commission taken out of a live session.

Payouts are weekly, after the 7-day hold, for at least $1. Each astrologer picks PayPal Payouts (`PAYPAL_ENV` `sandbox` or `live`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`; only their PayPal email is stored) or Stripe Connect Express. Razorpay Route is still only a later India option.

Chat is polled so it runs on Vercel. Voice is LiveKit. No phone numbers are exchanged.

## Android app

The Android app (`android/`, package `com.ikshana.drishti`) is a Trusted Web Activity over `https://dristi-six.vercel.app`. From 1.1.0 (versionCode 5) it opens `https://dristi-six.vercel.app/?shell=android`.

- **Recognising the app.** The page keeps the `?shell=android` mark in `sessionStorage` for that tab (`inAndroidApp()` in `web/index.html`) and sends `X-Drishti-Shell: android` on every API call. The same site opened later in Chrome is the plain website again. iOS still sends `X-Drishti-Shell: ios`.
- **What the Android app shows.** Live astrologers, the USD wallet (with a line that it pays only for live sessions and that Acharya is paid through Google Play), sign-in only for astrologers, no listing plan, Google Play wording for Acharya, and Android wording on privacy and support pages. Acharya keeps using Play billing exactly as in 1.0.x.
- **Server rules for `android`.** `/api/connect/apply` answers `403 signup_not_in_app`. Trial, plan and portal answer `403 plan_not_in_app` (as on iOS). Messages of an ended session answer `410 session_ended`. `/api/status` adds `stripe_google_pay`.
- **Voice.** Chrome asks for the microphone. During a call the page holds a Screen Wake Lock and asks people to keep Drishti open. The WebView fallback asks for `RECORD_AUDIO` and grants only audio, only to the Drishti site.
- **No push in 1.1.0.** Requests reach an astrologer only while the app is open, like iOS today.

### One database, two Vercel projects

`dristi` (Android and the website) uses the **same** Neon database as `drishti-ios`, so astrologers, wallets and earnings are shared. Run background work on **one** project only, `drishti-ios`:

- `CRON_SECRET` is set only on `drishti-ios`. Without it, `/api/cron/connect-payouts` on `dristi` answers 401 and does nothing, so payouts are never sent twice.
- The Stripe webhook endpoint and the LiveKit webhook URL point only at `drishti-ios`. The shared database means a top-up or call made in the Android app is still recorded there. Wallet top-ups are also credited directly when the browser confirms the payment.
- `APP_PUBLIC_URL` differs per project, so card 3-D Secure returns to the right site.

### Staging first: `drishti-android-staging`

A third Vercel project, `drishti-android-staging`, deploys `feature/android-connect` for testing. Give it a **separate** test database (a Neon branch of the iOS database, not the production one), Stripe test keys, PayPal sandbox, and the same LiveKit project. Build a test app that opens it by adding `drishtiHost=drishti-android-staging.vercel.app` to `android/gradle.properties` on your machine only (never commit it). Put the debug and upload key fingerprints in that project's `ANDROID_SHA256_FINGERPRINTS`.

### Data safety answers (Android)

| Data type | Collected | Shared | Why | Optional |
|---|---|---|---|---|
| Name, email (astrologers) | Yes | No | Account management | Required for astrologers |
| User IDs (random device id) | Yes | No | App functionality, fraud prevention | Required |
| Purchase history | Yes | No | App functionality (wallet, one purchase per question) | Required for purchases |
| Financial info: payment info | Collected by Stripe/PayPal, not stored by Drishti | Processed by Stripe/PayPal as service providers | Payments | Optional (only to add money) |
| Messages: other in-app messages | Yes | No (delivered to the chosen astrologer, which is the service) | App functionality, safety (reports) | Optional |
| Audio: voice | Not stored, carried live by LiveKit | No | App functionality | Optional |
| Other user-generated content: birth details | Yes | No | App functionality | Required for charts |

Data is encrypted in transit. Users can ask for deletion in the app (Profile > Delete account) or by email.
