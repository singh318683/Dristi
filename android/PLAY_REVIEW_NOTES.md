# Google Play review notes: Drishti 1.1.0 (versionCode 5)

Package `com.ikshana.drishti`. Drishti is a Trusted Web Activity over https://dristi-six.vercel.app. Version 1.1.0 adds live astrologers: a customer picks a named, approved person and talks to them by chat or voice, billed per minute.

## What changed in 1.1.0

- The app opens `https://dristi-six.vercel.app/?shell=android`. The `?shell=android` mark tells the site it is inside the Play app. The site sends `X-Drishti-Shell: android` on its API calls. Live astrologers appear only inside the app, not on the public website.
- `RECORD_AUDIO` and `MODIFY_AUDIO_SETTINGS` are declared for live voice calls. In the Trusted Web Activity, Chrome asks for the microphone when a voice session starts. In the WebView fallback (phones without a Trusted Web Activity browser), the app asks for `RECORD_AUDIO` and grants the microphone only to the Drishti site. The camera is never requested.
- No push notifications, no foreground service, no location, no contacts.
- versionCode 5, versionName 1.1.0.

## Payments (Payments policy)

| What | How it is paid | Why |
|---|---|---|
| Ask Acharya, the AI astrologer, one question | Google Play billing, one-time consumable `acharya_question`, $0.99 | Digital service. Unchanged from 1.0.x. |
| Live chat or voice session with a human astrologer | Wallet in US dollars, topped up with a card through Stripe ($5, $10 or $20). Google Pay through Stripe only if `ANDROID_GOOGLE_PAY_ENABLED=1`. | Google Play billing is not required for a 1:1 online paid service between two individuals that is not available for replay afterwards: https://support.google.com/googleplay/android-developer/answer/10281818 ("I'm a developer of an app that offers 1:1 online paid services"). |
| Astrologer listing plan ($9.99 a month or $99 a year) | Not sold in the Android app. Astrologers register and choose a plan on the website. | The app offers astrologers sign-in only. There is no button, link, or price for the listing plan inside the app (`plan_not_in_app`, `signup_not_in_app` on the server). |

How the live session meets the 1:1 conditions:

- **Between two individuals.** The customer picks one approved, named astrologer. The session is only that customer and that astrologer. Drishti keeps 5% of the session and the astrologer keeps 95%.
- **Not available for replay.** Voice calls run on LiveKit and are not recorded. In the Android app, a finished chat cannot be opened again: the server answers `410 session_ended` for the messages of an ended session. The messages are kept on the server only for reports, disputes, and account deletion.
- **The wallet is not virtual currency.** It is shown in US dollars, it pays only for live sessions, it cannot buy Acharya questions or any app feature, and it cannot be sent to another person. When the account is deleted, the remaining balance is refunded to the card.

The session places a five-minute hold at the astrologer's rate, bills per second of the accepted session (rounded down to the cent), and returns the unused hold. If nobody accepts within two minutes, the whole hold is returned.

## App access (for the reviewer)

Play Console > Policy and programs > App content > App access > "All or some functionality is restricted". Add one set of instructions:

- Name: `Live astrologer demo`
- Steps: "Open Drishti, finish the short birth-details screen with any date and place, open the Ask tab, then 'Talk to an astrologer'. Tap 'App Review code' and enter the code below. A demo astrologer appears with review credit, so no card is needed. Start a chat to see scripted replies, or a voice call to hear a short greeting. End the session to see the per-minute charge. Acharya questions are $0.99 through Google Play."
- Code: the value of `DEMO_REVIEW_CODE` on the server. Neetu types it into Play Console. It is not in this repo.

`DEMO_REVIEW=1` shows the demo to every device. Never set it on production (`dristi`). Use `DEMO_REVIEW_CODE` only.

## Play Console forms to update before release

- **Financial features:** "Mobile payments and digital wallets" (stored-value wallet for live sessions). Not a bank, not a money transfer app.
- **Data safety:** see the table in `docs/ASTROLOGER_CONNECT.md` (Android section).
- **Content rating:** answer again. Users can interact or exchange information (live chat and voice with an astrologer), with report and block. Digital purchases: yes.
- **Account deletion:** in-app (Profile > Delete account, and Astro > Delete astrologer account). Web URL: `https://dristi-six.vercel.app/support.html` ("Delete your account").
- **Store listing:** mention live astrologers, per-minute wallet billing in US dollars, Acharya $0.99 through Google Play, and 18+ to add money.

## Known limits in 1.1.0

- An astrologer sees a new request only while Drishti is open (no push yet). A request that nobody accepts in two minutes is refunded in full.
- Chrome on Android may pause the microphone if the screen locks or the user switches apps. The call screen asks people to keep Drishti open, and the page holds a Screen Wake Lock during a voice call.
