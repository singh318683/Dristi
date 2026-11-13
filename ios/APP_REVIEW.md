# App Review notes

Drishti gives a computed Vedic birth chart for free. One question to the AI astrologer, Acharya, is an in-app consumable, `acharya_question`, at $0.99.

A personal astrologer is optional. Chat and voice are bought with consumable wallet credit (`wallet_5`, `wallet_10`, `wallet_20`) through In-App Purchase. The server places a five-minute hold, bills by the minute on its own clock, and returns unused credit to the wallet. No phone number is shown or exchanged. Voice uses WebRTC inside the app.

The reading is guidance for reflection. It is not medical, legal, or financial advice. That line is on the checkout sheet.

A user can report or block an astrologer from the session. Reports are stored for review. Blocked astrologers leave that user's list. New astrologers cannot take sessions until an admin approves them and sets their rate.

The microphone is used only while a voice consultation the user started is connected. Background audio keeps that call audible if the phone is locked.
