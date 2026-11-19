"""Wallet, referral credits, magic-link sign-in, panchang and kundli PDF endpoints."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import app  # noqa: E402
import db  # noqa: E402
import engine  # noqa: E402
import llm  # noqa: E402
import pdfkundli  # noqa: E402

DELHI = {"name": "Delhi, India", "lat": 28.65195, "lon": 77.23149, "tz": "Asia/Kolkata"}
PROFILE = {"name": "Asha", "gender": "female", "date": "1990-05-15", "time": "10:30", "place": DELHI}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DRISHTI_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    monkeypatch.delenv("PLAY_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("PLAY_SERVICE_ACCOUNT_FILE", raising=False)
    app.clear_play_client()
    from fastapi.testclient import TestClient
    return TestClient(app.app)


def test_adapt_placeholders():
    assert db.adapt("select * from wallet where device=?", "sqlite").count("?") == 1
    assert db.adapt("select * from wallet where device=?", "postgres") == "select * from wallet where device=%s"
    assert db.dialect_of("") == "sqlite"
    assert db.dialect_of("postgres://u:p@localhost/db") == "postgres"
    assert db.dialect_of("postgresql://u:p@localhost/db") == "postgres"


def test_referral_does_not_grant_a_question(client):
    info = client.get("/api/referral", params={"device": "device-a"}).json()
    code = info["code"]
    assert len(code) == 8 and info["bonus"] == 0
    bad = client.post("/api/referral/attach", json={"device": "device-a", "code": code}).json()
    assert bad["ok"] is False and bad["reason"] == "invalid"
    assert client.post("/api/referral/attach", json={"device": "device-b", "code": "NOPE"}).json()["ok"] is False
    ok = client.post("/api/referral/attach", json={"device": "device-b", "code": code}).json()
    assert ok == {"ok": True, "bonus": 0}
    # An invite does not add credits or a free question.
    assert app.wallet("device-a")["credits"] == 0
    assert app.wallet("device-b")["free_left"] == 0
    assert app.wallet("device-b")["packs"] == {}
    assert app.wallet("device-b")["price"] == "0.99"


def test_attach_rejected_after_a_paid_question(client):
    code = app.referral_info("owner")["code"]
    with db.connect() as con:
        con.execute(
            "insert into ledger(device, ts, delta, reason) values(?,?,?,?)",
            ("late", "2026-10-31T00:00:00+00:00", 0, "acharya question"))
    got = app.attach_referral("late", code)
    assert got == {"ok": False, "reason": "not_new"}
    assert app.wallet("owner")["credits"] == 0


def test_magic_link_roundtrip_without_smtp(client):
    r = client.post("/api/auth/magic", json={"email": "Asha@Example.com", "device": "phone-1"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["delivered"] is False
    assert "token" not in body and "SMTP_HOST" in body["detail"]
    con = db.connect()
    try:
        row = con.execute("select token, email from magic_links").fetchone()
    finally:
        con.raw.close()
    token, email = row
    assert email == "asha@example.com"
    verified = client.get("/api/auth/verify", params={"token": token, "device": "phone-1"})
    assert verified.status_code == 200
    assert verified.json()["email"] == "asha@example.com"
    assert "session" in verified.json()
    me = client.get("/api/auth/me", params={"device": "phone-1"}).json()
    assert me["email"] == "asha@example.com"
    assert client.get("/api/auth/verify", params={"token": token, "device": "phone-1"}).status_code == 400


def test_otp_is_a_stub_and_packs_are_gone(client):
    otp = client.post("/api/auth/otp", json={"phone": "+910000000000", "device": "d"})
    assert otp.status_code == 501 and "TODO" in otp.json()["detail"]
    order = client.post("/api/wallet/order", json={"device": "d", "pack": "p100"})
    top = client.post("/api/wallet/topup", json={"device": "d", "pack": "p100"})
    assert order.status_code == top.status_code == 410
    assert "0.99" in order.json()["detail"] and "0.99" in top.json()["detail"]
    wallet = client.get("/api/wallet", params={"device": "d"}).json()
    assert wallet["credits"] == 0 and wallet["free_left"] == 0 and wallet["packs"] == {}
    assert wallet["price_label"] == "$0.99" and wallet["product_id"] == "acharya_question"


def test_panchang_honours_the_callers_civil_date(client):
    sat = client.get("/api/panchang", params={"lat": 28.65195, "lon": 77.23149, "tz": "Asia/Kolkata", "date": "2026-10-31"})
    sun = client.get("/api/panchang", params={"lat": 40.71, "lon": -74.0, "tz": "America/New_York", "date": "2026-10-31"})
    assert sat.status_code == sun.status_code == 200
    assert sat.json()["vaar"]["en"] == "Saturday"
    assert sun.json()["vaar"]["en"] == "Saturday"
    assert sat.json()["date"] == sun.json()["date"] == "2026-10-31"


def test_horoscope_date_follows_the_device_within_one_day(client):
    utc = datetime.now(timezone.utc).date()
    local = (utc - timedelta(days=1)).isoformat()
    near = client.get("/api/horoscope", params={"sign": "Cancer", "lang": "en", "date": local})
    assert near.status_code == 200 and near.json()["date"] == local
    far = (utc - timedelta(days=10)).isoformat()
    kept = client.get("/api/horoscope", params={"sign": "Cancer", "lang": "en", "date": far})
    assert kept.status_code == 200 and kept.json()["date"] == utc.isoformat()
    bad = client.get("/api/horoscope", params={"sign": "Cancer", "lang": "en", "date": "31-10-2026"})
    assert bad.status_code == 400


def test_background_music_never_starts():
    root = os.path.join(os.path.dirname(__file__), "..")
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    connect = open(os.path.join(root, "web", "connect.js"), encoding="utf-8").read()
    credits = open(os.path.join(root, "CREDITS.md"), encoding="utf-8").read()
    for gone in ("musicBtn", "musicBuild", "musicUnlock", "webkitAudioContext", "AudioContext", "musicOut", "drishtiUnlockAudio", "drishtiMusicHold", "createMediaStreamDestination"):
        assert gone not in html
    assert "drishtiMusicHold" not in connect and "drishtiMusicRelease" not in connect
    assert "does not play background music" in credits and "no third-party music license" in credits
    assert "drishti-v50" in open(os.path.join(root, "web", "sw.js"), encoding="utf-8").read()


def test_home_asks_for_the_device_date():
    html = open(os.path.join(os.path.dirname(__file__), "..", "web", "index.html"), encoding="utf-8").read()
    assert "function localISODate" in html
    assert html.count("localISODate()") >= 2
    assert 'id="tileAsk"' in html and 'id="tileToday"' in html and 'id="tileChart"' in html and 'id="tileMatch"' in html
    assert 'id="hDasha"' not in html and 'id="hHoro"' not in html and 'id="hPanch"' not in html


def test_home_periods_card_and_local_clock():
    html = open(os.path.join(os.path.dirname(__file__), "..", "web", "index.html"), encoding="utf-8").read()
    assert 'id="periods"' in html and 'id="periodsGo"' in html
    assert "S.pendingSheet = 'periods'" in html
    assert "\\uFE0E" in html
    assert "learning and growth" in html and "सीखना और विकास" in html
    assert "study and trade" in html and "अध्ययन और व्यापार" in html
    assert "new Mahadasha" in html and "next Antardasha" in html
    assert "Late night" in html and "Good night" in html and "शुभ रात्रि" in html
    assert "part === 'evening'" in html
    day = html.split("function dayPart", 1)[1].split("function greetLine", 1)[0]
    greet = html.split("function greetLine", 1)[1].split("function relWhen", 1)[0]
    assert "getHours()" in day and "getUTCHours" not in day
    assert "Good evening" in greet and "clock.getHours() < 5" in greet
    assert "function glanceTitle" in html and "function horoGlanceSentence" in html
    assert "glanceHoroLine" not in html
    assert "drishti-v50" in open(os.path.join(os.path.dirname(__file__), "..", "web", "sw.js"), encoding="utf-8").read()


def test_profile_screen_drops_invite_and_sign_in():
    html = open(os.path.join(os.path.dirname(__file__), "..", "web", "index.html"), encoding="utf-8").read()
    me = html.split("function pageMe()", 1)[1].split("async function captureLink", 1)[0]
    assert "Acharya is an AI astrologer." in me and "आचार्य एक AI ज्योतिषी हैं।" in me
    assert "Sign-in is optional" not in html and "साइन-इन वैकल्पिक है" not in html
    for gone in ("Invite link", "copyRef", "refCode", "Optional sign-in", "magicForm", "magicEmail", "otpBtn", "Phone OTP", "Email link", "function copyReferral"):
        assert gone not in html
    assert 'id="prefs"' in me and 'id="setLang"' in me and 'id="setSty"' in me
    assert 'id="personRow"' in me and 'id="credN"' in me and 'id="aboutBtn"' in me and 'id="clr"' in me
    assert "per Acharya question" in me and "Paid on Google Play" in me
    assert "pullSync" in html and "/api/auth/verify" in html and "/api/referral/attach" in html and "scheduleSync" in html
    assert 'id="musicBtn"' not in html and "function musicUnlock" not in html


def test_owned_purchase_lookup_uses_the_purchase_json_and_the_listener():
    root = os.path.join(os.path.dirname(__file__), "..", "android", "app", "src", "main", "java", "com", "ikshana", "drishti")
    activity = open(os.path.join(root, "PlayPaymentActivity.kt"), encoding="utf-8").read()
    ids = open(os.path.join(root, "PlayPurchaseIds.kt"), encoding="utf-8").read()
    service = open(os.path.join(root, "DrishtiDelegationService.kt"), encoding="utf-8").read()
    handler = open(os.path.join(root, "OwnedListPurchasesHandler.kt"), encoding="utf-8").read()
    updated = activity.split("override fun onPurchasesUpdated", 1)[1].split("if (result.responseCode == BillingClient.BillingResponseCode.USER_CANCELED)", 1)[0]
    assert "ownedToken(purchases)" in updated and "ITEM_ALREADY_OWNED" in updated
    assert updated.find("ownedToken(purchases)") < updated.find("ITEM_ALREADY_OWNED")
    assert "originalJson" in ids and "productId" in ids
    assert "live.size == 1" in ids
    assert "registerExtraCommandHandler(OwnedListPurchasesHandler" in service
    assert service.find("OwnedListPurchasesHandler") < service.find("super.onCreate()")
    assert 'commandName == "listPurchases"' in handler
    assert "PlayPurchaseIds.ids(purchase)" in handler
    assert "versionCode = 3" in open(os.path.join(os.path.dirname(__file__), "..", "android", "app", "build.gradle.kts"), encoding="utf-8").read()
    assert 'versionName = "1.0.2"' in open(os.path.join(os.path.dirname(__file__), "..", "android", "app", "build.gradle.kts"), encoding="utf-8").read()


def test_clientlog_does_not_print_purchase_tokens(client, capsys):
    secret = "supersecrettokenvalue1234567890"
    res = client.post("/api/clientlog", json={
        "event": "play-list-empty",
        "purchaseToken": secret,
        "listCount": 0,
        "listKeys": [["itemId"]],
    })
    assert res.status_code == 200 and res.json()["ok"] is True
    printed = capsys.readouterr().out
    assert "clientlog" in printed and secret not in printed and "listCount" in printed


def test_owned_play_question_is_sent_again_instead_of_bought():
    root = os.path.join(os.path.dirname(__file__), "..")
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    buy = html.split("function purchaseTokenOf", 1)[1].split("function noteAskError", 1)[0]
    assert "listPurchases" in buy and "getDetails" in buy
    assert "already own" in buy and "already bought" in buy and "ITEM_ALREADY_OWNED" in buy
    assert "listPurchaseHistory" in buy and "/api/clientlog" in html
    assert "listOwnedPlayToken" in buy and "buyFreshPlayQuestion" in buy and "playTokenKept" in buy
    ask = html.split("async function ask", 1)[1].split("const KOOTA", 1)[0]
    assert "buyPlayQuestion" in ask and "buyFreshPlayQuestion" in ask
    assert "payment_invalid" in ask and "keepPlayToken(purchaseToken)" in ask
    assert "response.complete('success')" in html
    assert "drishti-v50" in open(os.path.join(root, "web", "sw.js"), encoding="utf-8").read()


def test_answer_opens_in_a_reading_sheet():
    html = open(os.path.join(os.path.dirname(__file__), "..", "web", "index.html"), encoding="utf-8").read()
    assert "function openAnswerSheet" in html
    assert 'class="sheet answer"' in html and 'id="answerRead"' in html
    assert "body.answer-open .composer,body.answer-open .ask-stage,body.connect-open .composer,body.connect-open .ask-stage{display:none}" in html
    assert 'class="read-open"' in html and "Read this answer" in html and "यह उत्तर पढ़ें" in html
    ask = html.split("async function ask", 1)[1].split("const KOOTA", 1)[0]
    assert ask.find("purchaseToken = await takeToken(false)") < ask.find("openAnswerSheet(aiEl, true)")
    assert "openAnswerSheet(aiEl, true)" in ask
    assert "openAnswerSheet(painted, true)" in ask
    assert "id=\"askSend\"" in html and "priceLabel()" in html
    assert "drishti-v50" in open(os.path.join(os.path.dirname(__file__), "..", "web", "sw.js"), encoding="utf-8").read()


def test_rate_prompt_is_quiet_and_remembers_a_dismissal():
    html = open(os.path.join(os.path.dirname(__file__), "..", "web", "index.html"), encoding="utf-8").read()
    rate = html.split("const PLAY_STORE_ID", 1)[1].split("async function captureLink", 1)[0]
    assert "Enjoying Drishti?" in rate and "दृष्टि पसंद आ रही है?" in rate
    assert "Rate on Google Play" in rate and "Google Play पर रेट करें" in rate
    assert "Maybe later" in rate and "बाद में" in rate
    assert "market://details?id=" in rate and "https://play.google.com/store/apps/details?id=" in rate
    assert "com.ikshana.drishti" in rate
    assert "getDigitalGoodsService" in rate and "DrishtiPlay" in rate
    assert "v.days.length >= 3" in rate and "v.dismissals >= 3" in rate
    assert "7 * 86400000" in rate and "v.rated = true" in rate
    assert "S.streaming" in rate and "answer-open" in rate
    ask = html.split("async function ask", 1)[1].split("const KOOTA", 1)[0]
    assert ask.find("S.streaming = true") < ask.find("maybeShowRate()")
    assert "closeSheet({skipRate: true})" in ask
    closed = html.split("function closeSheet", 1)[1].split("function bindSheetSwipe", 1)[0]
    assert "answer-read" in closed and "v.read = true" in closed
    assert "drishti-v50" in open(os.path.join(os.path.dirname(__file__), "..", "web", "sw.js"), encoding="utf-8").read()
    assert "Rate on the App Store" in rate and "App Store पर रेट करें" in rate
    assert "apps.apple.com" in rate and "if (inIosApp())" in rate


def test_store_copy_follows_the_ios_shell():
    root = os.path.join(os.path.dirname(__file__), "..")
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    privacy = open(os.path.join(root, "web", "privacy.html"), encoding="utf-8").read()
    helper = html.split("function inIosApp()", 1)[1].split("function onboarding()", 1)[0]
    assert "getPlatform" in helper and "platform === 'ios'" in helper
    assert "return inIosApp() ? tr(iosEn, iosHi) : tr(playEn, playHi);" in helper
    pairs = [
        ("Each Acharya question is $0.99 on Google Play.", "Each Acharya question is $0.99 on the App Store."),
        ("आचार्य का हर प्रश्न Google Play पर $0.99 है।", "आचार्य का हर प्रश्न App Store पर $0.99 है।"),
        ("Pay with Google Play to ask.", "Pay with Apple to ask."),
        ("पूछने के लिए Google Play से भुगतान करें।", "पूछने के लिए Apple से भुगतान करें।"),
        ("Google Play did not accept that purchase", "The App Store did not accept that purchase"),
        ("Google Play ने यह खरीदारी स्वीकार नहीं की", "App Store ने यह खरीदारी स्वीकार नहीं की"),
        ("Google Play is still processing the payment", "Apple is still processing the payment"),
        ("Google Play अभी भुगतान पूरा कर रहा है", "Apple अभी भुगतान पूरा कर रहा है"),
        ("Play Billing is not ready on the server", "App Store billing is not ready on the server"),
        ("सर्वर पर Play Billing तैयार नहीं है", "सर्वर पर App Store बिलिंग तैयार नहीं है"),
        ("Google Play could not be reached", "The App Store could not be reached"),
        ("Google Play तक नहीं पहुँच सके", "App Store तक नहीं पहुँच सके"),
        ("Google Play is not available here", "The App Store is not available here"),
        ("यहाँ Google Play उपलब्ध नहीं है", "यहाँ App Store उपलब्ध नहीं है"),
        ("inside the Android app", "inside the iPhone app"),
        ("Android ऐप में $0.99 है", "iPhone ऐप में $0.99 है"),
        ("each, through Google Play.", "each, through the App Store."),
        ("प्रत्येक, Google Play से।", "प्रत्येक, App Store से।"),
        ("Paid on Google Play, one question at a time.", "Paid on the App Store, one question at a time."),
        ("Google Play पर भुगतान, एक बार में एक प्रश्न।", "App Store पर भुगतान, एक बार में एक प्रश्न।"),
        ("Rate on Google Play", "Rate on the App Store"),
        ("Google Play पर रेट करें", "App Store पर रेट करें"),
    ]
    for play, apple in pairs:
        assert play in html and apple in html
    assert "DrishtiPlay" in html and "getDigitalGoodsService" in html and "market://details?id=" in html
    assert 'data-store="play"' in privacy and 'data-store="ios" hidden' in privacy
    assert "Google Play handles the payment" in privacy and "Apple handles the payment" in privacy
    assert "Apple ID password" in privacy
    assert "आचार्य का हर प्रश्न" in privacy and "App Store से" in privacy and "भुगतान Apple करता है" in privacy
    assert "platform === 'ios'" in privacy
    script = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
const src = html.split('function inIosApp()')[1].split('function onboarding()')[0];
eval('function inIosApp()' + src);
function tr(en, hi) { return global.lang === 'hi' ? hi : en; }
function say() { return storeLine('on Google Play', 'Google Play पर', 'on the App Store', 'App Store पर'); }
global.window = {};
global.lang = 'en';
if (say() !== 'on Google Play') process.exit(2);
global.lang = 'hi';
if (say() !== 'Google Play पर') process.exit(3);
global.window = { Capacitor: { getPlatform() { return 'ios'; } } };
global.lang = 'en';
if (say() !== 'on the App Store') process.exit(4);
global.lang = 'hi';
if (say() !== 'App Store पर') process.exit(5);
global.window = { Capacitor: { getPlatform() { return 'android'; } } };
global.lang = 'en';
if (say() !== 'on Google Play') process.exit(6);
"""
    ran = subprocess.run(["node", "-e", script, os.path.join(root, "web", "index.html")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_live_astrologer_entries_are_visible_when_the_flag_is_on():
    root = os.path.join(os.path.dirname(__file__), "..")
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    connect = open(os.path.join(root, "web", "connect.js"), encoding="utf-8").read()
    me = html.split("function pageMe()", 1)[1].split("function peopleHtml", 1)[0]
    ask = html.split("function pageAsk()", 1)[1].split("function updateQuota", 1)[0]
    assert "Are you an astrologer?" in me and "क्या आप ज्योतिषी हैं?" in me
    assert "Register as a live astrologer" in me and "लाइव ज्योतिषी बनें" in me
    assert 'id="astroJoin"' in me and "location.hash = '#astro'" in me
    assert "S.status.astrologer_connect" in me
    assert "talkLiveHtml()" in ask
    assert "Talk to a live astrologer" in html and "लाइव ज्योतिषी से बात करें" in html
    assert 'id="talkLive"' in html and "DrishtiConnect.list" in html
    assert "function talkLiveHtml" in html and "astrologer_connect" in html.split("function talkLiveHtml", 1)[1].split("function bindTalkLive", 1)[0]
    assert 'id="portalBack"' in connect and "वापस" in connect
    assert "No astrologer is online right now." in connect and "अभी कोई ज्योतिषी ऑनलाइन नहीं है।" in connect
    assert "Check back in a little while." in connect and "थोड़ी देर बाद फिर देखें।" in connect
    assert 'id="connectEmpty"' in connect
    assert "days free. No card." in connect and "दिन मुफ़्त। कार्ड नहीं।" in connect
    assert "Monthly or yearly starts after the free trial ends." in connect
    assert "on the free trial until " in connect
    assert "Choose a plan to keep your listing after it ends." in connect
    assert "Your application is waiting for approval." in connect
    assert "no_billing_account" in connect and "stripe_not_configured" in connect
    assert "invalid_rate" in connect and "trial_unavailable" in connect
    assert "Something went wrong. Please try again." in connect
    assert "has_billing_account" in connect and "id=\"paidPlans\"" in connect
    assert "Forgot password?" in connect and "पासवर्ड भूल गए?" in connect
    assert "Contact Drishti support to reset your password." in connect
    assert "mailto:ikshanasolution123@gmail.com" in connect
    assert "Email or password is incorrect" in connect and "ईमेल या पासवर्ड गलत है" in connect
    assert "Reset password" in connect and "temporary_password" in connect
    assert "Set a new password" in connect and "SMTP_HOST" in connect
    assert "function clearPayForm" in connect and "stripePayBtn" in connect
    assert "setPacksDisabled(true)" in connect and "pay.disabled = true" in connect
    assert "/api/connect/wallet/confirm" in connect
    assert "function chime" in connect and "navigator.vibrate" in connect
    assert "New request" in connect and "नया अनुरोध" in connect
    assert "seenIncoming" in connect and "incoming-banner" in connect
    assert "openAstroSession" in connect and "astroSession" in connect
    assert "In a session" in connect and "सत्र में" in connect
    assert "No question yet" in connect and "अभी कोई प्रश्न नहीं" in connect
    assert "Seeker" in connect and "साधक" in connect
    assert "No one accepted in time." in connect
    assert "End session" in connect and "Back to dashboard" in connect
    assert "/api/connect/astro/live" in connect
    assert "payment_intent.succeeded" in open(os.path.join(root, "docs", "ASTROLOGER_CONNECT.md"), encoding="utf-8").read()
    assert "Processing…" in connect and "प्रक्रिया जारी…" in connect
    assert "applePay: 'never'" in connect and "googlePay: 'never'" in connect
    assert "confirmParams: {return_url: returnUrl}" in connect
    assert "30000, 'confirmPayment'" in connect and "30000, 'wallet-confirm'" in connect
    assert "Payment is taking longer than usual." in connect
    assert "function withTimeout" in connect and "function waitForBalance" in connect
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('function withTimeout');
const end = src.indexOf('function sleep');
function payLog() {}
eval(src.slice(start, end));
let timed = null;
withTimeout(new Promise(() => {}), 40, 'confirmPayment').catch(err => { timed = err; });
setTimeout(() => {
  if (!timed || timed.code !== 'timeout' || timed.step !== 'confirmPayment') process.exit(2);
  withTimeout(Promise.resolve('ok'), 200, 'wallet-confirm').then(value => {
    if (value !== 'ok') process.exit(3);
  }).catch(() => process.exit(4));
}, 80);
"""
    ran = subprocess.run(["node", "-e", script, os.path.join(root, "web", "connect.js")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_signup_rate_accepts_a_dollar_sign_and_errors_stay_readable():
    connect = open(os.path.join(os.path.dirname(__file__), "..", "web", "connect.js"), encoding="utf-8").read()
    assert "parseFloat(String($('#regRate').value).replace(/[^0-9.]/g, ''))" in connect
    assert "dollars < 0.3 || dollars > 5" in connect
    assert "for example 1.50" in connect and "जैसे 1.50" in connect
    assert "function detailText" in connect and "item.loc[item.loc.length - 1]" in connect
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('function detailText');
const end = src.indexOf('function api(');
eval(src.slice(start, end));
const arr = [
  {loc: ['body', 'rate_cents'], msg: 'Input should be a valid integer'},
  {loc: ['body', 'email'], msg: 'Field required'},
];
if (detailText(arr) !== 'rate_cents: Input should be a valid integer; email: Field required') process.exit(2);
if (detailText('unauthorized') !== 'unauthorized') process.exit(3);
if (detailText({message: 'Voice is not configured on the server.'}) !== 'Voice is not configured on the server.') process.exit(4);
const obj = detailText({error: 'plan_required', hold_cents: 500});
if (!obj || obj === '[object Object]' || obj.indexOf('plan_required') < 0) process.exit(5);
const dollars = raw => parseFloat(String(raw).replace(/[^0-9.]/g, ''));
if (dollars('$1') !== 1 || dollars('$1.50') !== 1.5 || dollars('1') !== 1) process.exit(6);
if (isFinite(dollars('')) || isFinite(dollars('abc'))) process.exit(7);
"""
    ran = subprocess.run(["node", "-e", script, os.path.join(os.path.dirname(__file__), "..", "web", "connect.js")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_short_wallet_points_at_a_pack_and_disabled_buttons_look_disabled():
    root = os.path.join(os.path.dirname(__file__), "..")
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    connect = open(os.path.join(root, "web", "connect.js"), encoding="utf-8").read()
    assert "button:disabled,.btn:disabled{opacity:.45;cursor:not-allowed}" in html
    assert "connect-packs" in html and ".btn.pack" in html and ".btn.pack.suggest" in html
    assert "Add money to start" in connect and "शुरू करने के लिए पैसे जोड़ें" in connect
    assert "Tap an amount above." in connect and "ऊपर कोई राशि चुनें।" in connect
    assert "function coverPack" in connect
    assert 'id="connectStart" ${short ? \'disabled\' : \'\'}' not in connect
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('function coverPack');
const end = src.indexOf('function dollars');
eval(src.slice(start, end));
if (coverPack(150) !== 'wallet_5') process.exit(2);
if (coverPack(500) !== 'wallet_5') process.exit(3);
if (coverPack(501) !== 'wallet_10') process.exit(4);
if (coverPack(1500) !== 'wallet_20') process.exit(5);
if (coverPack(2500) !== 'wallet_20') process.exit(6);
if (coverPack(0) !== 'wallet_5') process.exit(7);
"""
    ran = subprocess.run(["node", "-e", script, os.path.join(root, "web", "connect.js")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_planet_detail_opens_in_a_sheet():
    root = os.path.join(os.path.dirname(__file__), "..")
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    assert "function openReading" in html
    assert "sheet reading" in html and "read-m" in html and "sheet-handle" in html
    assert "Drag down to close" in html and "बंद करने के लिए नीचे खींचें" in html
    assert "· house" in html
    assert "max-height:58px" not in html
    assert "body.app-fit .info .m{display:-webkit-box" not in html
    assert "if (o) openReading(o, src)" in html
    assert "drishti-v50" in open(os.path.join(root, "web", "sw.js"), encoding="utf-8").read()


def test_panchang_and_pdf_endpoints(client):
    pan = client.get("/api/panchang", params={"lat": 28.65195, "lon": 77.23149, "tz": "Asia/Kolkata", "date": "2026-10-27"})
    assert pan.status_code == 200
    body = pan.json()
    assert body["vaar"]["en"] == "Tuesday" and body["rahu_kaal"]["eighth"] == 7
    assert client.get("/api/panchang", params={"lat": 28.6, "lon": 77.2, "tz": "Nope/Nope"}).status_code == 400
    pdf = client.post("/api/kundli.pdf?lang=en", json=PROFILE)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.content.startswith(b"%PDF")
    hi = client.post("/api/kundli.pdf?lang=hi", json=PROFILE)
    assert hi.status_code == 200 and hi.content.startswith(b"%PDF")
    chart = app.chart_for(app.Profile(**PROFILE))
    en = pdfkundli.sections(chart, "en")
    hi_lines = pdfkundli.sections(chart, "hi")
    text = "\n".join(en)
    assert "Asha" in text and "Vimshottari" in text and "Manglik" in text
    assert "Sun" in text and "House 1:" in text
    hi_text = "\n".join(hi_lines)
    assert "कुंडली" in hi_text and "आचार्य" in hi_text and "Asha" in hi_text


def test_horoscope_is_cached_per_sign_and_language(client, monkeypatch):
    calls = []

    async def fake_complete(messages, max_tokens=400):
        calls.append(messages[0]["content"])
        payload = json.loads(messages[1]["content"])
        sign, lang = payload["sign"], payload["lang"]
        house = payload["transits"]["Moon"]["house_from_rashi"]
        assert engine.SIGNS_HI[engine.SIGNS.index(sign)] in messages[0]["content"]
        if lang == "hi":
            name = engine.SIGNS_HI[engine.SIGNS.index(sign)]
            return f"{name} राशि का राशिफल। चंद्रमा {house}वें भाव में।"
        return f"{sign} Horoscope. The Moon moves through your {house}th house."

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "complete", fake_complete)

    cancer = client.get("/api/horoscope", params={"sign": "Cancer", "lang": "en"})
    pisces = client.get("/api/horoscope", params={"sign": "Pisces", "lang": "en"})
    pisces_hi = client.get("/api/horoscope", params={"sign": "Pisces", "lang": "hi"})
    assert cancer.status_code == pisces.status_code == pisces_hi.status_code == 200
    assert "no-store" in cancer.headers["cache-control"]
    c, p, h = cancer.json(), pisces.json(), pisces_hi.json()
    assert c["sign"] == "Cancer" and p["sign"] == "Pisces" and h["lang"] == "hi"
    assert c["date"] == p["date"] == h["date"]
    assert c["text"] != p["text"] != h["text"]
    assert "Cancer" in c["text"] and "Pisces" not in c["text"]
    assert "Pisces" in p["text"] and "Cancer" not in p["text"]
    assert "मीन" in h["text"] and "Pisces" not in h["text"]
    assert c["transits"]["Moon"]["sign"] == p["transits"]["Moon"]["sign"]
    assert c["transits"]["Moon"]["house_from_rashi"] != p["transits"]["Moon"]["house_from_rashi"]
    assert str(c["transits"]["Moon"]["house_from_rashi"]) in c["text"]
    assert str(p["transits"]["Moon"]["house_from_rashi"]) in p["text"]
    assert len(calls) == 3

    again = client.get("/api/horoscope", params={"sign": "Pisces", "lang": "en"}).json()
    assert again["text"] == p["text"] and len(calls) == 3

    with db.connect() as con:
        con.execute("update horoscope set body=? where day=? and sign=? and lang=?",
                    ("Cancer Horoscope, 27 October 2026. Today the Moon moves through your 11th house.",
                     p["date"], "Pisces", "en"))
    repaired = client.get("/api/horoscope", params={"sign": "Pisces", "lang": "en"}).json()
    assert "Pisces" in repaired["text"] and "Cancer Horoscope" not in repaired["text"]
    assert len(calls) == 4
    kept = client.get("/api/horoscope", params={"sign": "Cancer", "lang": "en"}).json()
    assert kept["text"] == c["text"] and len(calls) == 4


SUDHANSHU = {"name": "sudhanshu singh", "gender": "male", "date": "1981-05-10", "time": "19:30",
             "place": {"name": "Along, Arunachal Pradesh, India", "lat": 28.16951, "lon": 94.8006, "tz": "Asia/Kolkata"}}


def test_payout_account_request_drops_the_legacy_type_field():
    source = open(os.path.join(os.path.dirname(__file__), "..", "server", "connect_stripe.py"), encoding="utf-8").read()
    api = open(os.path.join(os.path.dirname(__file__), "..", "server", "connect_api.py"), encoding="utf-8").read()
    body = source.split("def connected_account_body", 1)[1].split("def account_link_body", 1)[0]
    assert '"type": "express"' not in body
    assert '"losses_collector": "stripe"' in body
    assert '"fees_collector": "application"' in body
    assert '"dashboard": "express"' in body
    assert '"country": "US"' in body
    assert "stripe_transfers" in body and '"requested": True' in body
    link = source.split("def account_link_body", 1)[1].split("def transfers_active", 1)[0]
    assert "configurations" not in link
    assert '"refresh_url": refresh_url' in link and '"return_url": return_url' in link
    assert '"fields": "currently_due"' in link
    assert "eventually_due" not in link
    assert "v2/core/accounts" in source and "v2/core/account_links" in source
    assert "application/json" in source
    assert "account_onboarding" in source
    assert 'ACCOUNT_API_VERSION = "2026-10-28.preview"' in source
    onboard = api.split("def api_stripe_onboard", 1)[1].split("def api_quote", 1)[0]
    assert '"acct-astro-v2-" + astro_id' in onboard
    assert '"acct-" + astro_id' not in onboard
    transfer = source.split("def create_transfer", 1)[1]
    assert '"destination": destination' in transfer and '"currency": "usd"' in transfer


def test_chart_endpoint_stays_lahiri_even_if_the_caller_thread_is_fagan(client):
    import swisseph as swe
    swe.set_sid_mode(swe.SIDM_FAGAN_BRADLEY, 0, 0)
    try:
        got = client.post("/api/chart", json=SUDHANSHU)
    finally:
        swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)
    assert got.status_code == 200
    body = got.json()
    assert 23.4 < body["ayanamsa"]["value"] < 23.8
    assert body["lagna"]["sign"] == "Scorpio" and body["lagna"]["degree"] > 18
    assert body["dasha"]["current"]["mahadasha"]["start"] == "2024-06-29"
    assert body["dasha"]["current"]["antardasha"]["lord"] == "Rahu"


def test_script_urls_match_the_service_worker_and_are_not_cached(client):
    root = os.path.join(os.path.dirname(__file__), "..")
    sw = open(os.path.join(root, "web", "sw.js"), encoding="utf-8").read()
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    connect = open(os.path.join(root, "web", "connect.js"), encoding="utf-8").read()
    cache = sw.split("const CACHE = '", 1)[1].split("'", 1)[0]
    assert cache == "drishti-v50"
    assert 'src="birthpicker.js?v=' + cache + '"' in html
    assert 'src="connect.js?v=' + cache + '"' in html
    assert "cache: 'no-store'" in sw
    for path in ("/", "/index.html", "/connect.js", "/birthpicker.js", "/sw.js"):
        assert "'" + path + "'" in sw.split("const FRESH", 1)[1].split("]", 1)[0]
    vercel = json.load(open(os.path.join(root, "vercel.json"), encoding="utf-8"))
    headers = {item["source"]: {h["key"]: h["value"] for h in item["headers"]} for item in vercel["headers"]}
    for path in ("/", "/index.html", "/connect.js", "/birthpicker.js", "/sw.js"):
        assert headers[path]["Cache-Control"] == "no-cache"
        assert client.get(path).headers["cache-control"] == "no-cache"
    assert "function requireVoiceRoom" in connect
    assert "The voice room is not connected yet." in connect and "वॉइस रूम अभी जुड़ा नहीं है।" in connect
    mute = connect.split("function bindMute", 1)[1].split("function bindSpeaker", 1)[0]
    assert mute.find("requireVoiceRoom") < mute.find("setAttribute")
    speaker = connect.split("function bindSpeaker", 1)[1].split("function setVoiceNote", 1)[0]
    assert speaker.find("requireVoiceRoom") < speaker.find("setAttribute")
    assert '.call-controls .btn.quiet[aria-pressed="true"]' in html
    assert "function primeCallAudio" in connect and "AudioPlaybackStatusChanged" in connect
    assert "function paintVoiceDebug" in connect and "canPlaybackAudio" in connect
    assert "room ' + ((room && room.state) || 'none')" in connect
    ios = json.load(open(os.path.join(root, "ios", "capacitor.config.json"), encoding="utf-8"))
    assert ios["ios"]["allowsInlineMediaPlayback"] is True
    assert ios["ios"]["mediaTypesRequiringUserActionForPlayback"] == []
    bridge = open(os.path.join(root, "ios", "plugins", "DrishtiNative", "DrishtiBridgeViewController.swift"), encoding="utf-8").read()
    assert "allowsInlineMediaPlayback = true" in bridge
    assert "mediaTypesRequiringUserActionForPlayback = []" in bridge
