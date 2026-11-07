"""Wallet, referral credits, magic-link sign-in, panchang and kundli PDF endpoints."""
import json
import os
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


def test_background_music_is_one_synthesized_drone():
    root = os.path.join(os.path.dirname(__file__), "..")
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    credits = open(os.path.join(root, "CREDITS.md"), encoding="utf-8").read()
    assert 'id="musicBtn"' in html
    assert html.count("function musicBuild") == 1
    assert "if (musicCtx) return" in html
    assert "webkitAudioContext" in html
    assert "return musicPhone() ? 0.55 : 0.25" in html
    assert "musicRamp(musicLevel(), fresh ? 3 : 0.8)" in html
    assert "visibilitychange" in html and "pageshow" in html
    assert "createPeriodicWave" in html and "const sa = 196" in html
    assert "createMediaStreamDestination" in html and "playsinline" in html
    assert "audioSession" in html and "interrupted" in html
    assert "createBuffer(1, 1" in html
    assert "touchstart" in html and "touchend" in html
    assert "audiodebug=1" in html and "audioDebug" in html
    assert "drishtiUnlockAudio" in html
    assert ".mp3" not in html and ".ogg" not in html and ".m4a" not in html
    assert "Web Audio" in credits and "no third-party music license" in credits
    assert "drishti-v16" in open(os.path.join(root, "web", "sw.js"), encoding="utf-8").read()


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
    assert "drishti-v16" in open(os.path.join(os.path.dirname(__file__), "..", "web", "sw.js"), encoding="utf-8").read()


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
    assert 'id="musicBtn"' in html and "function musicUnlock" in html


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
    assert "drishti-v16" in open(os.path.join(root, "web", "sw.js"), encoding="utf-8").read()


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
    assert "drishti-v16" in open(os.path.join(root, "web", "sw.js"), encoding="utf-8").read()


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
