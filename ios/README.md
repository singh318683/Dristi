# Drishti for iOS

Capacitor shell for the iPhone app. Bundle id `com.ikshana.drishti`. The personal-astrologer screens appear only after `ASTROLOGER_CONNECT_ENABLED` is on. See [docs/ASTROLOGER_CONNECT.md](../docs/ASTROLOGER_CONNECT.md).

This app does not load the production site, https://dristi-six.vercel.app. It loads a separate Vercel project, for example `drishti-ios`, deployed from `feature/astrologer-connect`.

## Site URL

The workflow reads the repository variable `IOS_SERVER_URL` and writes it into `capacitor.config.json` before `npx cap add ios`. When the variable is unset, the shell uses `https://drishti-ios.vercel.app`. Set the variable to the separate Vercel project that deploys this branch. The step refuses `dristi-six.vercel.app`.

## Generate the Xcode project

```bash
cd ios
npm install
npx cap add ios
python3 scripts/prepare_xcode.py ios/App \
  --additions Info.plist.additions.xml \
  --swift plugins/DrishtiNative/DrishtiStore.swift
```

Capacitor always names the native project `ios`, so the Xcode project is `ios/ios/App`. Open `ios/ios/App/App.xcworkspace`.

`prepare_xcode.py` merges `Info.plist.additions.xml` (microphone, background audio, and voip) and adds `plugins/DrishtiNative/DrishtiStore.swift` to the App target. The page calls `window.DrishtiStore.purchase(productId)`.

StoreKit sells one product:

| Id | What it buys |
|---|---|
| `acharya_question` | One $0.99 Acharya question |

Create that as a consumable in App Store Connect. The server checks each transaction with the App Store Server API. The phone's receipt alone is not enough. Live-session wallet top-ups are Stripe, including Apple Pay, not StoreKit.

`DrishtiCall` reports the WebRTC session to CallKit. The audio path is the in-app LiveKit room, not a phone number.

## GitHub Actions

`.github/workflows/ios.yml` runs on macOS and can be started with **Run workflow**. The unsigned job always builds with `CODE_SIGNING_ALLOWED=NO`.

The signed job uses App Store Connect automatic signing. No `.p12` certificate and no provisioning profile are stored in GitHub. It runs the archive, export, and TestFlight upload only when these repository secrets are all set. Otherwise that job stops after a check and succeeds.

| Secret | Use |
|---|---|
| `APPLE_TEAM_ID` | Apple Developer team id. Also written into `ExportOptions.plist`. |
| `APPSTORE_API_KEY_ID` | App Store Connect API key id |
| `APPSTORE_API_ISSUER_ID` | Issuer id |
| `APPSTORE_API_KEY` | Contents of the `.p8` file |

The signed archive sets `CFBundleVersion` and `CURRENT_PROJECT_VERSION` to `github.run_number`.

The server uses the same issuer id and a separate in-app purchase key (`APPLE_IAP_PRIVATE_KEY`) to verify Acharya transactions. Do not put that key in the app.

### App Store Connect

1. Identifiers: register the App ID `com.ikshana.drishti`.
2. Apps: create the app record with that bundle id.
3. Users and Access → Integrations → App Store Connect API. Generate a key with **App Manager** or **Admin** access. Download the `.p8` once. The key id and the issuer id are on that page.
4. In this GitHub repository, add the four secrets above. Paste the `.p8` file into `APPSTORE_API_KEY`.
5. Add the repository variable `IOS_SERVER_URL` for the separate Vercel project.
6. Actions → iOS → Run workflow. Xcode creates the distribution certificate and profile in the cloud (`-allowProvisioningUpdates`), exports an `.ipa` with method `app-store-connect` and automatic signing, then `xcrun altool --upload-app` sends it to TestFlight.

App Review notes are in [APP_REVIEW.md](APP_REVIEW.md).
