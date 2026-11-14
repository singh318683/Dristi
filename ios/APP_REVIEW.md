# App Review notes

The iPhone app is Drishti plus live astrologers. The website and Android app do not include live astrologers.

One question to the AI astrologer, Acharya, is an in-app consumable, `acharya_question`, at $0.99, purchased with StoreKit. That is the only In-App Purchase.

A live astrologer is a person-to-person service. The customer pays with a wallet topped up through Stripe, including Apple Pay, not through StoreKit. This follows guideline 3.1.3(d): the service is a real-time consultation with an identified person, delivered outside a digital unlock of app content. Chat and voice stay inside the app so the timer, the per-minute price, and the end of the session are enforced. No phone number is shown or exchanged.

There is review risk. Apple can decide the consultation is still a digital service that must use In-App Purchase. The notes for the reviewer should say the astrologer is a specific person, the customer chooses them, and the session is billed by the minute for that person's time. If Apple rejects that reading, the live-session checkout would have to move to StoreKit before release. The Acharya question would stay on StoreKit either way.

Providers pay Drishti a web subscription (a free trial, then monthly or yearly) through Stripe Billing, not through the app. Drishti keeps 5% of each session. The provider keeps 95%, paid out through Stripe Connect.

The reading is guidance for reflection. It is not medical, legal, or financial advice. A user can report or block an astrologer. New providers cannot take sessions until an admin approves them. The microphone is used only during a voice consultation the user started.
