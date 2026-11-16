# Personal astrologers

The iPhone app is a separate product: the same Drishti as the website and Android app, plus live astrologers. It is a second Vercel project, for example `drishti-ios`, deploying **only** from `feature/astrologer-connect` (and later its release branch) with `ASTROLOGER_CONNECT_ENABLED=1`. The production project that serves https://dristi-six.vercel.app stays on `main` with the flag unset, so Android and the current site do not change.

The iOS shell does not load that production site. GitHub Actions writes the repository variable `IOS_SERVER_URL` into `ios/capacitor.config.json` before generating the Xcode project. Leave it unset and the shell uses `https://drishti-ios.vercel.app`. Set it to the separate Vercel project deployed from this branch. See [ios/README.md](../ios/README.md).

## Switch

| Variable | Default | Purpose |
|---|---|---|
| `ASTROLOGER_CONNECT_ENABLED` | off | `1` shows registration, the connect sheet, and the API. |
| `DATABASE_URL` | unset | Postgres. Neon: the **pooled** URL (host contains `-pooler`). Without it the flag cannot open a wallet and the rest of the app keeps working. |
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

Customer payments for a live session are Stripe, including Apple Pay. They are person-to-person services under App Store guideline 3.1.3(d), not in-app purchases. StoreKit remains only for the $0.99 Acharya question. See `ios/APP_REVIEW.md` for the review risk.

## Who can be booked

A provider sets one rate, from the minimum through the maximum, for both chat and call. Neetu approves, suspends, or removes them. They appear online only when they are approved, toggled online, and either inside the trial, on an active subscription, or inside the grace period after a failed payment.

Stripe webhooks update that state: `customer.subscription.created`, `updated`, and `deleted`, and `invoice.payment_failed`.

## Money

Wallet packs `wallet_5`, `wallet_10`, and `wallet_20` are Stripe PaymentIntents. After the browser confirms a payment, the server reads that PaymentIntent from Stripe and credits it. The Stripe webhook must include `payment_intent.succeeded` and uses the same payment id, so a retry does not credit the wallet twice. A session holds five minutes at the provider's stored rate. The server clock starts when they accept. Heartbeats and the LiveKit webhook end the session. The charge is elapsed seconds times the rate, floored to the cent, and never more than the hold. Unused hold returns to the wallet.

Of the gross, Drishti takes `PLATFORM_COMMISSION_RATE` and the provider the remainder. Twelve minutes at $1.00 is $12.00 gross, $0.60 Drishti, $11.40 provider. Amounts are integer cents and the two shares sum to the gross. There is no 75/25 split and no App Store commission taken out of a live session.

Payouts are Stripe Connect Express, weekly, after the 7-day hold, for at least $1. Razorpay Route is still only a later India option.

Chat is polled so it runs on Vercel. Voice is LiveKit. No phone numbers are exchanged.
