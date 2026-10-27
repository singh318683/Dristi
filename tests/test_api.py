"""Wallet, referral credits, magic-link sign-in, panchang and kundli PDF endpoints."""
import json
import os
import sys

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
    monkeypatch.setenv("REFERRAL_BONUS", "15")
    from fastapi.testclient import TestClient
    return TestClient(app.app)


def test_adapt_placeholders():
    assert db.adapt("select * from wallet where device=?", "sqlite").count("?") == 1
    assert db.adapt("select * from wallet where device=?", "postgres") == "select * from wallet where device=%s"
    assert db.dialect_of("") == "sqlite"
    assert db.dialect_of("postgres://u:p@localhost/db") == "postgres"
    assert db.dialect_of("postgresql://u:p@localhost/db") == "postgres"


def test_referral_bonus_on_first_question(client):
    code = client.get("/api/referral", params={"device": "device-a"}).json()["code"]
    assert len(code) == 8
    bad = client.post("/api/referral/attach", json={"device": "device-a", "code": code}).json()
    assert bad["ok"] is False and bad["reason"] == "invalid"
    assert client.post("/api/referral/attach", json={"device": "device-b", "code": "NOPE"}).json()["ok"] is False
    ok = client.post("/api/referral/attach", json={"device": "device-b", "code": code}).json()
    assert ok["ok"] is True and ok["bonus"] == 15
    assert app.charge("device-b") == "free"
    assert app.wallet("device-a")["credits"] == 15
    assert app.wallet("device-b")["credits"] == 15
    assert app.charge("device-b") == "free"
    assert app.wallet("device-a")["credits"] == 15
    assert app.wallet("device-b")["credits"] == 15
    late = client.post("/api/referral/attach", json={"device": "device-c", "code": code}).json()
    assert late["ok"] is True
    app.charge("device-c")
    app.charge("device-c")
    # a second attach after the first question cannot stack another bonus
    again = app.attach_referral("device-c", code)
    assert again["ok"] is False
    assert app.wallet("device-a")["credits"] == 30  # b and c


def test_attach_rejected_after_first_question(client):
    code = app.referral_info("owner")["code"]
    assert app.charge("late") == "free"
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


def test_otp_and_razorpay_are_stubs(client):
    otp = client.post("/api/auth/otp", json={"phone": "+910000000000", "device": "d"})
    assert otp.status_code == 501 and "TODO" in otp.json()["detail"]
    order = client.post("/api/wallet/order", json={"device": "d", "pack": "p100"})
    assert order.status_code == 501 and "RAZORPAY_KEY_ID" in order.json()["detail"]
    assert app.payment_provider() == "demo"
    top = client.post("/api/wallet/topup", json={"device": "d", "pack": "p100"})
    assert top.status_code == 200 and top.json()["demo"] is True and top.json()["credits"] == 100


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
