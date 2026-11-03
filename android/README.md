# Drishti for Android

This folder is an Android Studio project. It wraps https://dristi-six.vercel.app as a Trusted Web Activity. The package name is `com.ikshana.drishti`. The app name on the phone is Drishti.

An Acharya question is the Play consumable `acharya_question` at $0.99. Chrome shows the Play purchase sheet through the Digital Goods API (`https://play.google.com/billing`). The purchase token goes to the website, and the server checks it with Google. This app does not acknowledge or consume the purchase. The server consumes it after an answer has started, so a failed answer can be tried again with the same purchase.

If the phone has no browser that can run a Trusted Web Activity, the app opens the site in a WebView and sets `window.DrishtiPlay.purchase` and `window.DrishtiPlay.listPurchases`. That path does not consume the purchase either.

The project uses Android Gradle Plugin 9.1.0 and Gradle 9.3.1, which is the Gradle version the AGP 9.1 release notes list as both the minimum and the default. Kotlin is the built-in compiler from AGP 9.1 (Kotlin Gradle plugin 2.2.10 on that compatibility table). `minSdk` is 24. `compileSdk` and `targetSdk` are 36, with build-tools 36.0.0. The screen is portrait. Colours match the website: cream `#FAF7F2`, saffron `#B35A00`, indigo `#1F1A4D`.

No keystore and no password belong in this folder. `local.properties` is created by Android Studio on your machine and is not committed.

## Open it and run it

1. Install current stable Android Studio. On first launch, let it install Android SDK 36.
2. Choose File, then Open, and select this `android` folder (the folder that contains `settings.gradle.kts`).
3. After pulling a new version, choose File, then Sync Project with Gradle Files. The first sync downloads Gradle 9.3.1 and the libraries. If Studio still mentions AGP 9.5, sync again after the pull so it reads 9.1.0.
4. Plug in a phone with USB debugging, or create an emulator with a Play Store system image (API 24 or newer).
5. If Drishti from an older build is already on the phone, uninstall it first, then press Run. The app opens the live site. With no network, it shows an offline screen and a Try again button. A build from before the site-settings activity was declared crashed on open on Android 12; uninstalling clears that install.

A debug build can show the site. A $0.99 purchase works only after the app is installed from a Play testing track, the product exists, and you are a license tester. That is a Play rule, not something the debug button can skip.

## Release checklist

This is the first Play upload: `versionCode` 1, `versionName` `1.0.0`. Leave Android Gradle Plugin at 9.1.0. Do not commit a keystore. `*.jks` and `*.keystore` are gitignored.

1. Generate the upload keystore. In Android Studio choose Build, then Generate Signed App Bundle or APK. Choose Android App Bundle, then Next. Under Key store path choose Create new. Save the `.jks` outside this repo (for example in your home folder). Use alias `upload`. Remember both passwords. Play needs this same file for every later update.
2. Build the signed bundle. On that same screen choose the release build type and finish. Android Studio writes the signed `app-release.aab`. Upload that file. `./gradlew bundleRelease` without a keystore only writes an unsigned bundle, and Play will reject it.
3. Create the app in Play Console with package name `com.ikshana.drishti` if it is not there yet. The phone label is Drishti. The app opens https://dristi-six.vercel.app.
4. Upload the signed `.aab` to Internal testing. Add yourself as a tester and install from the opt-in link on a phone with the Play Store. A sideloaded copy cannot complete a purchase.
5. Copy the fingerprints into Vercel. After the upload, open Play Console, then App integrity. Copy the App signing key certificate SHA-256. Also copy the upload key SHA-256 (the same value as `keytool -list -v -keystore /path/to/your-upload.jks -alias upload`, the line that starts with `SHA256:`). On the Vercel project set `ANDROID_SHA256_FINGERPRINTS` to both, separated by a comma. Until this is set, `/.well-known/assetlinks.json` is `[]` and the browser bar stays.
6. Create the in-app product `acharya_question` at $0.99 (United States) and activate it. Choose consumable if the form asks. Add the phone’s Gmail under License testing.
7. On Vercel set `PLAY_SERVICE_ACCOUNT_JSON` to the Play service-account key (the whole JSON, or base64) and `DATABASE_URL` to the pooled Postgres URL. Without the Play key every Acharya question is refused. Without the database, accounts and charts are wiped on each cold start.
8. Redeploy the site. Open `https://dristi-six.vercel.app/.well-known/assetlinks.json` and confirm both fingerprints are listed. Reinstall from the Internal testing link. The address bar should be gone. Ask one question: Play should show $0.99. After the answer, a second question should ask for $0.99 again.

A license-tester purchase is not a free question for anyone else. A desktop browser still cannot start a Play purchase.
