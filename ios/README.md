# Drishti for iOS

Capacitor loads https://dristi-six.vercel.app. Bundle id `com.ikshana.drishti`. The personal-astrologer screens appear only after `ASTROLOGER_CONNECT_ENABLED` is on. See [docs/ASTROLOGER_CONNECT.md](../docs/ASTROLOGER_CONNECT.md).

## Generate the Xcode project

```bash
cd ios
npm install
npx cap add ios
```

Then merge `Info.plist.additions.xml` (microphone and background audio) and add `plugins/DrishtiNative/DrishtiStore.swift` to the App target. The page calls `window.DrishtiStore.purchase(productId)`. Product ids:

| Id | What it buys |
|---|---|
| `acharya_question` | One $0.99 Acharya question |
| `wallet_5` | $5 consultation wallet |
| `wallet_10` | $10 consultation wallet |
| `wallet_20` | $20 consultation wallet |

Create those as consumable in App Store Connect. The server checks each transaction with the App Store Server API. The phone's receipt alone is not enough.

`DrishtiCall` reports the WebRTC session to CallKit. The audio path is the in-app LiveKit room, not a phone number.

## GitHub Actions

`.github/workflows/ios.yml` runs on macOS. The default job is unsigned (`CODE_SIGNING_ALLOWED=NO`).

A signed archive needs these secrets, and the repository variable `APPLE_TEAM_ID` so the signed job is allowed to run:

| Secret | Use |
|---|---|
| `APPLE_TEAM_ID` | Signing team |
| `APPLE_CERTIFICATE_P12` | Distribution certificate, base64 |
| `APPLE_CERTIFICATE_PASSWORD` | Password for that certificate |
| `APPLE_PROVISION_PROFILE` | App Store profile, base64 |
| `APPSTORE_API_KEY_ID` | App Store Connect API key id |
| `APPSTORE_API_ISSUER_ID` | Issuer id |
| `APPSTORE_API_KEY` | The `.p8` key |

The server uses the same issuer id and a separate in-app purchase key (`APPLE_IAP_PRIVATE_KEY`) to verify transactions. Do not put that key in the app.

App Review notes are in [APP_REVIEW.md](APP_REVIEW.md).
