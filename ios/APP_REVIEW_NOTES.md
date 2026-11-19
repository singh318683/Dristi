# App Review notes

The steps under "For App Review" are what to paste into App Store Connect. Replace each `<placeholder>` with the value configured on the iOS server. Do not commit those values.

## For the developer

Set these on the **iOS** Vercel project only. Do not set them on the Android / Play project. The two apps should keep separate data.

| Env var | Purpose |
| --- | --- |
| `DEMO_REVIEW_CODE` | Code the reviewer types to see the demo on that phone. Leave `DEMO_REVIEW` unset in production so other people never see the demo. |
| `DEMO_REVIEW` | Optional. Set to `1` only on a server used for review, if every device on that server should see the demo without a code. |
| `DEMO_ASTRO_EMAIL` | Sign-in email for Astrologer mode. Use an address that is not a real astrologer's account. At least one `@`. |
| `DEMO_ASTRO_PASSWORD` | Sign-in password for that account. At least 8 characters. The server hashes it. It is not stored in the app. |

Also keep the existing LiveKit variables (`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`) set on that server, or the demo call cannot play the greeting.

The demo astrologer is stored as **offline**. An older Android build that shares a database will not list it. Review credit is a separate balance. It cannot pay a real astrologer, and the demo never creates a payout, Stripe transfer, or PayPal payout.

**Astrologer listing plan.** Live chat and voice are person-to-person consultations (Stripe, guideline 3.1.3(d)). The Acharya question is In-App Purchase. The monthly or yearly fee an astrologer pays to keep a listing is still charged with Stripe inside the app. That fee is not a person-to-person consultation. App Review may decide it must use In-App Purchase. Confirm that before you submit, or be ready for Apple to ask about it. This build does not change that fee.

## For App Review

Drishti is a Vedic chart app with an optional marketplace of live astrologers. You do not need a real astrologer online, and you do not need a card.

### Payments

- Live chat and live voice are consultations between the user and an independent astrologer. The user pays from a wallet funded by card through Stripe. We offer this under guideline 3.1.3(d) (person-to-person services).
- The review demo does not charge a card. It uses review credit, described below.
- Each question to Acharya, the in-app AI astrologer, is a digital feature sold with In-App Purchase at $0.99. The chart, matching, and horoscope are free.
- Astrologers can also pay a monthly or yearly listing fee through Stripe. That fee is separate from the live consultation and from the Acharya question.

### See the demo astrologer

1. Open the app and finish the short birth-details setup. Any date and place is fine.
2. Open the Ask tab.
3. Tap **Talk to a live astrologer**.
4. Tap **App Review code** and enter: `<DEMO_REVIEW_CODE>`
5. **Demo Astrologer (App Review)** appears. The label says it is not a real person. It is hidden from everyone else.

### Add review credit

Tap **Add review credit**. The app adds $5. No card is used. This credit only pays the demo astrologer. It is not sent to a real person and it does not create a payout.

You can also add it from the chat or call sheet if the balance is too low to start. The button says **Add review credit**, not a dollar pack.

### Chat

Tap **Chat**, then **Start chat**. The demo accepts immediately and sends a few short scripted replies. You can type a message; the replies stay on the script. Nothing is a live person.

### Call

Tap **Call**, then **Start call**. Allow the microphone. The demo joins the voice room and plays a short greeting on a loop, so you can hear that audio works. The greeting is synthesized. The call is not recorded. End the call when you have heard it.

The rate is the minimum, $0.30 per minute, taken from the review credit. A five-minute hold is placed and the unused part is returned when you end the call.

### Astrologer mode

1. Open Profile and tap **Are you an astrologer?**, or open the Astrologer screen.
2. Tap **I already have an account**.
3. Email: `<DEMO_ASTRO_EMAIL>`
4. Password: `<DEMO_ASTRO_PASSWORD>`
5. You are in Astrologer mode, with Dashboard, Sessions, Earnings, and Settings.

This is a shared review login. It cannot change the payout account, buy a listing plan, change the rate, or delete the shared account. Those actions are refused so the login keeps working for the next reviewer and cannot pay anyone.

### Delete account

On Profile, tap **Delete account**. The sheet explains what is removed and that any unused card balance is refunded to the card. Review credit is only cleared. Type `DELETE`, then confirm.

If a chat or call is still open, end it first. The app will ask you to.

Please use this Profile delete for the account-deletion review. Do not delete the shared demo astrologer; that login has to stay available.

### Report and block

During the demo chat or call, tap **Report**. Choose Harassment, Sexual content, or Scam or payment, or type a short reason, then send.

**Block** hides the demo astrologer on this phone. Please try Report first. If you block the demo and need it back, enter the App Review code again.
