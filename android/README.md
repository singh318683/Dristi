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

## Create the upload key and build a signed bundle

1. In Android Studio choose Build, then Generate Signed App Bundle or APK.
2. Choose Android App Bundle, then Next.
3. Choose Create new… under Key store path. Save the file outside the git repo, for example in your home folder. Do not put it in this project.
4. Fill in the keystore password, the key alias (for example `upload`), the key password, and your name. Android Studio writes a `.jks` file.
5. Choose the release build type and finish. Android Studio writes `app-release.aab`.

Keep the `.jks` file and both passwords. Play uses this upload key for every future update. If you lose it, you cannot ship an update with the same app unless Play App Signing still has the upload key registered and you ask Google to reset it.

To check that the project itself builds before you create a key, from this folder:

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
./gradlew assembleDebug bundleRelease
```

`assembleDebug` writes a debug APK. `bundleRelease` writes an unsigned `.aab` because no keystore is configured in Gradle. The file you upload to Play is the signed bundle from the menu above, not the unsigned one.

## Fingerprints, so the URL bar goes away

The site serves `https://dristi-six.vercel.app/.well-known/assetlinks.json`. The statement lists package `com.ikshana.drishti` and the SHA-256 fingerprints from the server variable `ANDROID_SHA256_FINGERPRINTS` (comma-separated). You need two fingerprints: the upload key you just created, and the app-signing key that Play creates.

Upload key, from the `.jks` file:

```bash
keytool -list -v -keystore /path/to/your-upload.jks -alias upload
```

Copy the line that starts with `SHA256:`.

Play app-signing key, after the first bundle is uploaded:

1. Open Play Console, then the Drishti app.
2. Open Test and release, then App integrity (older consoles: Setup, then App signing).
3. Under App signing key certificate, copy SHA-256.
4. Under Upload key certificate, confirm it matches the `keytool` line. If Play shows a different upload certificate, use the one Play shows.

On the Vercel project for https://dristi-six.vercel.app, set:

```text
ANDROID_SHA256_FINGERPRINTS=AA:BB:...upload...,CC:DD:...play signing...
```

Redeploy. Open `https://dristi-six.vercel.app/.well-known/assetlinks.json` and check that both fingerprints are there. Then reinstall the app from the Play testing link. The browser bar at the top should be gone. Until this variable is set, the file is `[]` and the bar stays. That is expected.

## Internal testing and a $0.99 question

1. In Play Console create the app with package name `com.ikshana.drishti` if it is not there yet.
2. Create the one-time product `acharya_question` at $0.99 in the United States, and activate it. Choose consumable if the form asks.
3. Upload the signed `.aab` to Internal testing, add yourself as a tester, and install from the opt-in link. Use a Play Store image, not a sideloaded copy, when you want to test billing.
4. Under Settings, then License testing, add the Gmail account on the phone.
5. On the server, `PLAY_SERVICE_ACCOUNT_JSON` must be set or the question is refused even after Play takes the payment. The purchase stays unconsumed, so you can try again after the key is added.
6. Ask Acharya one question. Play should show $0.99. After the answer, ask a second question. Play should ask for $0.99 again.

License-tester purchases are not a free question for everyone else. A desktop browser still cannot start a Play purchase.
