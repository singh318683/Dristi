"""The Android Trusted Web Activity: launch mark, microphone, version, and Play review notes."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android"
MANIFEST = ANDROID / "app" / "src" / "main" / "AndroidManifest.xml"
GRADLE = ANDROID / "app" / "build.gradle.kts"
SRC = ANDROID / "app" / "src" / "main" / "java" / "com" / "ikshana" / "drishti"
NOTES = ANDROID / "PLAY_REVIEW_NOTES.md"
DOCS = ROOT / "docs" / "ASTROLOGER_CONNECT.md"


def _meta(manifest: str, name: str) -> str:
    found = re.search(r'android:name="' + re.escape(name) + r'"\s+android:value="([^"]+)"', manifest)
    assert found, name
    return found.group(1)


def test_launch_url_marks_the_android_app():
    manifest = MANIFEST.read_text(encoding="utf-8")
    gradle = GRADLE.read_text(encoding="utf-8")
    assert _meta(manifest, "android.support.customtabs.trusted.DEFAULT_URL") == "https://${siteHost}/?shell=android"
    # Site settings still open the plain origin.
    assert _meta(manifest, "android.support.customtabs.trusted.MANAGE_SPACE_URL") == "https://${siteHost}/"
    # Release builds open production unless drishtiHost is set on the build machine.
    assert '?: "dristi-six.vercel.app"' in gradle
    assert 'manifestPlaceholders["siteHost"] = siteHost' in gradle
    assert 'buildConfigField("String", "SITE_ORIGIN", "\\"https://$siteHost/\\"")' in gradle
    assert "buildConfig = true" in gradle
    assert "drishtiHost" not in (ANDROID / "gradle.properties").read_text(encoding="utf-8")
    site = (SRC / "SiteActivity.kt").read_text(encoding="utf-8")
    assert "private const val ORIGIN = BuildConfig.SITE_ORIGIN" in site
    assert 'private const val START_URL = ORIGIN + "?shell=android"' in site
    assert "web.loadUrl(START_URL)" in site and "web.loadUrl(ORIGIN)" not in site


def test_microphone_permission_for_voice_and_nothing_more():
    manifest = MANIFEST.read_text(encoding="utf-8")
    assert '<uses-permission android:name="android.permission.RECORD_AUDIO" />' in manifest
    assert '<uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />' in manifest
    for absent in ("CAMERA", "POST_NOTIFICATIONS", "FOREGROUND_SERVICE", "READ_CONTACTS", "ACCESS_FINE_LOCATION"):
        assert "android.permission." + absent not in manifest, absent
    # Play Billing stays for the Acharya question.
    assert "com.android.vending.BILLING" in manifest
    assert "https://play.google.com/billing" in manifest


def test_webview_fallback_grants_only_the_microphone_to_drishti():
    site = (SRC / "SiteActivity.kt").read_text(encoding="utf-8")
    assert "override fun onPermissionRequest(request: PermissionRequest)" in site
    answer = site.split("private fun answerMic(request: PermissionRequest)", 1)[1].split("override fun onPurchasesUpdated", 1)[0]
    assert "request.origin?.host == Uri.parse(ORIGIN).host" in answer
    assert "PermissionRequest.RESOURCE_AUDIO_CAPTURE" in answer
    assert "request.deny()" in answer
    assert "RESOURCE_VIDEO_CAPTURE" not in site
    assert "request.grant(request.resources)" not in site
    assert "Manifest.permission.RECORD_AUDIO" in answer and "askMic.launch" in answer
    assert "ActivityResultContracts.RequestPermission()" in site
    assert "mediaPlaybackRequiresUserGesture = false" in site
    # Billing is untouched: the app still never consumes a purchase.
    assert "consumeAsync(" not in site and "acknowledgePurchase(" not in site


def test_version_is_1_1_0_code_5():
    gradle = GRADLE.read_text(encoding="utf-8")
    assert "versionCode = 5" in gradle
    assert 'versionName = "1.1.0"' in gradle
    assert 'applicationId = "com.ikshana.drishti"' in gradle


def test_play_review_notes_cover_payments_policy_and_review_access():
    notes = NOTES.read_text(encoding="utf-8")
    for needle in (
        "1:1 online paid service",
        "https://support.google.com/googleplay/android-developer/answer/10281818",
        "acharya_question",
        "Google Play billing",
        "US dollars",
        "not recorded",
        "cannot be opened again",
        "DEMO_REVIEW_CODE",
        "Mobile payments and digital wallets",
        "RECORD_AUDIO",
        "versionCode 5",
        "listing plan",
    ):
        assert needle in notes, needle
    assert "DEMO_REVIEW=1" in notes  # documented as: never on production


def test_docs_name_every_android_switch():
    docs = DOCS.read_text(encoding="utf-8")
    for name in ("ASTROLOGER_CONNECT_PLATFORMS", "ANDROID_GOOGLE_PAY_ENABLED", "CONNECT_LOCK_ENDED_CHATS",
                 "X-Drishti-Shell", "?shell=android", "drishti-android-staging", "PAYPAL_ENV"):
        assert name in docs, name
