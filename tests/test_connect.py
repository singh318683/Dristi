"""Astrologer sessions: prices, holds, splits, idempotency, and who may start a call."""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import app  # noqa: E402
import db  # noqa: E402
import connect_livekit  # noqa: E402
import connect_money as money  # noqa: E402
import connect_paypal  # noqa: E402
import connect_store as store  # noqa: E402
import connect_storekit  # noqa: E402
import connect_stripe  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DRISHTI_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ASTROLOGER_CONNECT_ENABLED", "1")
    monkeypatch.setenv("ASTROLOGER_CONNECT_ALLOW_SQLITE", "1")
    monkeypatch.setenv("ADMIN_TOKEN", "admin-test-token")
    monkeypatch.setenv("PLATFORM_COMMISSION_RATE", "0.05")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    from fastapi.testclient import TestClient
    return TestClient(app.app)


def auth(token):
    return {"Authorization": "Bearer " + token}


def seed(client, rate=100):
    created = client.post("/api/connect/apply", json={
        "email": "meera@example.com", "password": "correct-horse", "name": "Meera Sharma",
        "specialties": ["career", "relationships"], "languages": ["Hindi", "English"],
        "years": 12, "bio": "Timing and work.", "rate_cents": rate,
    })
    assert created.status_code == 200 and created.json()["rate_cents"] == rate
    admin = auth("admin-test-token")
    listing = client.get("/api/connect/admin/astrologers", headers=admin).json()["astrologers"]
    astro_id = listing[0]["id"]
    decided = client.post("/api/connect/admin/astrologers/" + astro_id, headers=admin, json={"action": "approve"})
    assert decided.status_code == 200 and decided.json()["rate_cents"] == rate
    login = client.post("/api/connect/astro/login", json={"email": "meera@example.com", "password": "correct-horse"}).json()
    assert client.post("/api/connect/astro/trial", headers=auth(login["token"])).status_code == 200
    assert client.post("/api/connect/astro/online", headers=auth(login["token"]), json={"online": True}).status_code == 200
    return astro_id, login["token"]


def fund(device, product, payment_id, provider="stripe"):
    return store.credit_topup(device, provider, product, payment_id)


def test_prices_and_split_ignore_floats():
    assert money.platform_bps() == 500
    assert money.rate_bounds() == (30, 500)
    assert money.check_rate(100) == 100
    with pytest.raises(money.MoneyError):
        money.check_rate(29)
    with pytest.raises(money.MoneyError):
        money.check_rate(501)
    assert money.product_cents("wallet_5") == 500
    assert money.product_cents("wallet_20") == 2000
    with pytest.raises(money.MoneyError):
        money.product_cents("wallet_1")
    assert money.hold_cents(100) == 500
    assert money.draw_buckets(300, 400, 500) == (300, 200)
    assert money.elapsed_cost(100, 720) == 1200
    assert money.elapsed_cost(100, 0) == 0
    provider, platform = money.split_gross(1200)
    assert (provider, platform) == (1140, 60)
    assert provider + platform == 1200


def test_feature_stays_off_without_the_flag(client, monkeypatch):
    monkeypatch.delenv("ASTROLOGER_CONNECT_ENABLED", raising=False)
    assert client.get("/api/status").json()["astrologer_connect"] is False
    assert client.get("/api/connect/astrologers", params={"device": "phone"}).status_code == 404


def test_sqlite_without_permission_does_not_open_a_wallet(client, monkeypatch):
    monkeypatch.delenv("ASTROLOGER_CONNECT_ALLOW_SQLITE", raising=False)
    assert client.get("/api/status").json()["astrologer_connect"] is False
    assert client.get("/api/connect/wallet", params={"device": "phone"}).status_code == 503


def test_client_cannot_set_the_price(client):
    astro_id, _token = seed(client)
    fund("phone", "wallet_5", "pi_price")
    refused = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "chat", "rate_cents": 1,
    })
    assert refused.status_code == 422
    assert store.wallet_view("phone")["balance_cents"] == 500


def test_hold_refund_and_idempotent_start(client):
    astro_id, _token = seed(client, rate=100)
    fund("phone", "wallet_5", "pi_hold")
    body = {"device": "phone", "astrologer_id": astro_id, "mode": "chat", "question": "Career?", "idempotency_key": "once"}
    first = client.post("/api/connect/sessions", json=body)
    assert first.status_code == 200
    assert first.json()["hold_cents"] == 500
    assert first.json()["rate_cents"] == 100
    assert store.wallet_view("phone")["balance_cents"] == 0
    again = client.post("/api/connect/sessions", json=body)
    assert again.json()["id"] == first.json()["id"]
    assert store.wallet_view("phone")["balance_cents"] == 0
    second = client.post("/api/connect/sessions", json={**body, "idempotency_key": "twice"})
    assert second.status_code == 402
    ended = client.post("/api/connect/sessions/" + first.json()["id"] + "/end", json={"device": "phone"})
    assert ended.status_code == 200
    assert ended.json()["status"] == "ended"
    assert ended.json()["cost_cents"] == 0
    assert store.wallet_view("phone")["balance_cents"] == 500
    ended_again = client.post("/api/connect/sessions/" + first.json()["id"] + "/end", json={"device": "phone"})
    assert ended_again.json()["cost_cents"] == 0
    assert store.wallet_view("phone")["balance_cents"] == 500


def test_bill_splits_stripe_and_apple_and_is_idempotent(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "now", lambda: base)
    astro_id, token = seed(client, rate=100)
    fund("phone", "wallet_5", "pi_stripe_bill")
    started = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "chat", "idempotency_key": "stripe-bill",
    }).json()
    assert client.post("/api/connect/sessions/" + started["id"] + "/accept", headers=auth(token)).status_code == 200
    monkeypatch.setattr(store, "now", lambda: base + timedelta(seconds=90))
    ended = client.post("/api/connect/sessions/" + started["id"] + "/end", json={"device": "phone"}).json()
    assert ended["cost_cents"] == 150
    assert store.wallet_view("phone")["balance_cents"] == 350
    earned = client.get("/api/connect/astro/earnings", headers=auth(token)).json()
    assert earned["sessions"][0]["astro_cents"] == 143
    assert earned["sessions"][0]["platform_cents"] == 7
    assert earned["net_cents"] == 143 and earned["fee_cents"] == 7
    client.post("/api/connect/sessions/" + started["id"] + "/end", json={"device": "phone"})
    assert store.wallet_view("phone")["balance_cents"] == 350
    assert client.get("/api/connect/astro/earnings", headers=auth(token)).json()["net_cents"] == 143


def test_balance_ends_the_session(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 11, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "now", lambda: base)
    astro_id, token = seed(client, rate=100)
    fund("phone", "wallet_5", "pi_balance")
    started = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "chat", "idempotency_key": "balance",
    }).json()
    client.post("/api/connect/sessions/" + started["id"] + "/accept", headers=auth(token))
    monkeypatch.setattr(store, "now", lambda: base + timedelta(seconds=301))
    view = client.post("/api/connect/sessions/" + started["id"] + "/heartbeat", json={"role": "user", "device": "phone"}).json()
    assert view["status"] == "ended" and view["end_reason"] == "balance"
    assert view["cost_cents"] == 500
    assert store.wallet_view("phone")["balance_cents"] == 0


def test_auth_and_call_token_gating(client, monkeypatch):
    astro_id, token = seed(client, rate=100)
    fund("phone", "wallet_10", "pi_call")
    stranger = client.post("/api/connect/astro/login", json={"email": "meera@example.com", "password": "nope"})
    assert stranger.status_code == 401
    assert client.get("/api/connect/admin/astrologers").status_code == 401
    chat = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "chat", "idempotency_key": "chat-token",
    }).json()
    assert client.post("/api/connect/sessions/" + chat["id"] + "/call-token", json={"device": "phone"}).status_code == 403
    assert client.get("/api/connect/sessions/" + chat["id"], params={"device": "other"}).status_code == 401
    call = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "call", "idempotency_key": "call-token",
    }).json()
    assert client.post("/api/connect/sessions/" + call["id"] + "/call-token", json={"device": "phone"}).status_code == 403
    client.post("/api/connect/sessions/" + call["id"] + "/accept", headers=auth(token))
    assert client.post("/api/connect/sessions/" + call["id"] + "/call-token", json={"device": "phone"}).status_code == 503
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://live.example")
    issued = client.post("/api/connect/sessions/" + call["id"] + "/call-token", json={"device": "phone"})
    assert issued.status_code == 200
    claims = connect_livekit.read_jwt(issued.json()["token"], "secret")
    video = claims["video"]
    assert video["room"] == issued.json()["room"]
    assert video["roomJoin"] is True and video["canPublish"] is True and video["canSubscribe"] is True
    assert claims["sub"].startswith("user-")
    assert claims["exp"] - claims["nbf"] <= 610
    astro_tok = client.post("/api/connect/sessions/" + call["id"] + "/call-token", json={"device": ""}, headers=auth(token))
    assert astro_tok.status_code == 200
    astro_claims = connect_livekit.read_jwt(astro_tok.json()["token"], "secret")
    astro_video = astro_claims["video"]
    assert astro_video["room"] == issued.json()["room"]
    assert astro_video["roomJoin"] is True and astro_video["canPublish"] is True and astro_video["canSubscribe"] is True
    assert astro_claims["sub"].startswith("astro-")
    body = json.dumps({"event": "room_finished", "room": {"name": issued.json()["room"]}}).encode()
    header = connect_livekit.webhook_token("secret", body)
    closed = client.post("/api/connect/livekit/webhook", content=body, headers={"Authorization": header})
    assert closed.status_code == 200
    assert client.get("/api/connect/sessions/" + call["id"], params={"device": "phone"}).json()["status"] == "ended"
    forged = client.post("/api/connect/livekit/webhook", content=body, headers={"Authorization": "nope"})
    assert forged.status_code == 401


def test_stripe_webhook_credits_once(client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    payload = json.dumps({"type": "payment_intent.succeeded", "data": {"object": {
        "id": "pi_once", "metadata": {"purpose": "wallet", "device": "phone", "product_id": "wallet_10"},
    }}}).encode()
    stamp = str(int(time.time()))
    sig = hmac.new(b"whsec_test", stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    headers = {"Stripe-Signature": f"t={stamp},v1={sig}"}
    first = client.post("/api/connect/stripe/webhook", content=payload, headers=headers)
    second = client.post("/api/connect/stripe/webhook", content=payload, headers=headers)
    assert first.status_code == 200 and first.json()["credited"] is True
    assert second.json()["credited"] is False
    assert store.wallet_view("phone")["stripe_cents"] == 1000
    bad = client.post("/api/connect/stripe/webhook", content=payload, headers={"Stripe-Signature": f"t={stamp},v1=00"})
    assert bad.status_code == 400


def test_ringing_waits_two_minutes_then_refunds(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "now", lambda: base)
    astro_id, token = seed(client, rate=100)
    fund("phone", "wallet_5", "pi_ring")
    started = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "chat", "question": "",
        "profile": {"name": "Anuprita Khanna", "date": "1990-05-15", "time": "10:30", "place": {"name": "Delhi"}},
        "idempotency_key": "ring",
    }).json()
    assert started["status"] == "ringing" and started["cost_cents"] == 0
    assert started["seeker_name"] == "Anuprita"
    monkeypatch.setattr(store, "now", lambda: base + timedelta(seconds=60))
    mid = client.post("/api/connect/sessions/" + started["id"] + "/heartbeat", json={"role": "user", "device": "phone"}).json()
    assert mid["status"] == "ringing" and mid["cost_cents"] == 0
    assert store.wallet_view("phone")["balance_cents"] == 0
    monkeypatch.setattr(store, "now", lambda: base + timedelta(seconds=120))
    done = client.post("/api/connect/sessions/" + started["id"] + "/heartbeat", json={"role": "user", "device": "phone"}).json()
    assert done["status"] == "ended" and done["end_reason"] == "unanswered" and done["cost_cents"] == 0
    assert store.wallet_view("phone")["balance_cents"] == 500
    headers = auth(token)
    accepted_late = client.post("/api/connect/sessions/" + started["id"] + "/accept", headers=headers)
    assert accepted_late.status_code == 409


def test_wallet_activity_is_the_last_ten_for_this_device(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 11, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "now", lambda: base)
    astro_id, token = seed(client, rate=100)
    fund("phone", "wallet_5", "pi_act_5")
    fund("phone", "wallet_10", "pi_act_10")
    fund("other", "wallet_20", "pi_act_other")
    started = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "chat", "idempotency_key": "activity",
    }).json()
    assert client.post("/api/connect/sessions/" + started["id"] + "/accept", headers=auth(token)).status_code == 200
    monkeypatch.setattr(store, "now", lambda: base + timedelta(seconds=275))
    ended = client.post("/api/connect/sessions/" + started["id"] + "/heartbeat", json={"role": "user", "device": "phone"}).json()
    assert ended["status"] == "ended" and ended["cost_cents"] == 458
    view = client.get("/api/connect/wallet", params={"device": "phone"}).json()
    assert view["balance_cents"] == 1042
    assert [(item["kind"], item["cents"], item["amount_label"]) for item in view["activity"]] == [
        ("refund", 142, "+$1.42"),
        ("charge", -100, "-$1.00"),
        ("hold", -500, "-$5.00"),
        ("topup", 1000, "+$10.00"),
        ("topup", 500, "+$5.00"),
    ]
    assert view["activity"][0]["end_reason"] == "astro_left" and view["activity"][2]["mode"] == "chat"
    assert all(item["cents"] != 2000 for item in view["activity"])
    other = client.get("/api/connect/wallet", params={"device": "other"}).json()
    assert [(item["kind"], item["cents"]) for item in other["activity"]] == [("topup", 2000)]
    quiet_base = base + timedelta(hours=1)
    monkeypatch.setattr(store, "now", lambda: quiet_base)
    fund("quiet", "wallet_5", "pi_quiet")
    ringing = client.post("/api/connect/sessions", json={
        "device": "quiet", "astrologer_id": astro_id, "mode": "call", "idempotency_key": "quiet-ring",
    }).json()
    monkeypatch.setattr(store, "now", lambda: quiet_base + timedelta(seconds=120))
    done = client.post("/api/connect/sessions/" + ringing["id"] + "/heartbeat", json={"role": "user", "device": "quiet"}).json()
    assert done["end_reason"] == "unanswered" and done["cost_cents"] == 0
    returned = client.get("/api/connect/wallet", params={"device": "quiet"}).json()
    assert returned["balance_cents"] == 500
    assert [(item["kind"], item["cents"], item["end_reason"]) for item in returned["activity"]] == [
        ("refund", 500, "unanswered"),
        ("hold", -500, ""),
        ("topup", 500, ""),
    ]
    assert returned["activity"][1]["mode"] == "call"
    for n in range(12):
        fund("stack", "wallet_5", "pi_stack_" + str(n))
    stacked = client.get("/api/connect/wallet", params={"device": "stack"}).json()["activity"]
    assert len(stacked) == 10
    assert [item["id"] for item in stacked] == sorted((item["id"] for item in stacked), reverse=True)
    assert client.get("/api/connect/wallet", params={"device": "fresh"}).json()["activity"] == []
    connect = open(os.path.join(os.path.dirname(__file__), "..", "web", "connect.js"), encoding="utf-8").read()
    html = open(os.path.join(os.path.dirname(__file__), "..", "web", "index.html"), encoding="utf-8").read()
    assert 'id="walletCard"' in html and "paintWallet" in html
    assert "astrologer_connect" in html.split('id="walletCard"', 1)[0].rsplit("pageMe", 1)[-1]
    assert "function paintWallet" in connect and "function openWalletTopup" in connect
    assert 'data-pack="wallet_5"' in connect and 'data-pack="wallet_10"' in connect and 'data-pack="wallet_20"' in connect
    assert "Add money" in connect and "पैसे जोड़ें" in connect
    assert "Added money" in connect and "पैसे जोड़े" in connect
    assert "Per-minute charge" in connect and "प्रति मिनट शुल्क" in connect
    assert "Hold returned · no one accepted" in connect and "होल्ड लौटी · कोई स्वीकार नहीं किया" in connect
    assert "Voice call hold" in connect and "वॉइस कॉल की होल्ड" in connect
    assert "No wallet activity yet." in connect and "अभी कोई वॉलेट गतिविधि नहीं।" in connect
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('function walletWhen');
const end = src.indexOf('function walletCardHtml');
let S = {lang: 'en'};
const tr = (en, hi) => (S.lang === 'hi' ? hi : en);
eval(src.slice(start, end));
if (walletWhen('2026-11-18T20:00:00+00:00').indexOf('Nov') < 0) process.exit(2);
if (walletActivityText({kind: 'topup'}) !== 'Added money') process.exit(3);
if (walletActivityText({kind: 'charge'}) !== 'Per-minute charge') process.exit(4);
if (walletActivityText({kind: 'refund', end_reason: 'unanswered'}).indexOf('no one accepted') < 0) process.exit(5);
if (walletActivityText({kind: 'hold', mode: 'call'}) !== 'Voice call hold') process.exit(6);
S.lang = 'hi';
if (walletActivityText({kind: 'refund'}) !== 'होल्ड लौटी') process.exit(7);
"""
    ran = subprocess.run(["node", "-e", script, os.path.join(os.path.dirname(__file__), "..", "web", "connect.js")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_pending_session_clock_stays_still_until_started_at(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "now", lambda: base)
    astro_id, token = seed(client, rate=100)
    fund("phone", "wallet_5", "pi_clock")
    started = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "chat", "idempotency_key": "clock",
    }).json()
    assert started["status"] == "ringing" and started["started_at"] is None and started["elapsed_seconds"] == 0
    monkeypatch.setattr(store, "now", lambda: base + timedelta(seconds=40))
    waiting = client.post("/api/connect/sessions/" + started["id"] + "/heartbeat", json={"role": "user", "device": "phone"}).json()
    assert waiting["status"] == "ringing" and waiting["elapsed_seconds"] == 0 and waiting["started_at"] is None
    monkeypatch.setattr(store, "now", lambda: base + timedelta(seconds=50))
    accepted = client.post("/api/connect/sessions/" + started["id"] + "/accept", headers=auth(token)).json()
    assert accepted["started_at"]
    monkeypatch.setattr(store, "now", lambda: base + timedelta(seconds=95))
    beat = client.post("/api/connect/sessions/" + started["id"] + "/heartbeat", json={"role": "user", "device": "phone"}).json()
    assert beat["status"] == "active" and beat["elapsed_seconds"] == 45 and beat["started_at"] == accepted["started_at"]
    connect = open(os.path.join(os.path.dirname(__file__), "..", "web", "connect.js"), encoding="utf-8").read()
    beat_fn = connect.split("async function beat()", 1)[1].split("function mergeById", 1)[0]
    assert "live.at = Date.now()" not in beat_fn and "live.elapsed" not in beat_fn
    assert "function syncClock" in connect and "if (clock) return" in connect
    assert "clearInterval(clock)" in connect
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('function clockReading');
const end = src.indexOf('function paintClock');
eval(src.slice(start, end));
const startAt = '2026-11-18T12:00:50+00:00';
const startMs = Date.parse(startAt);
const pending = clockReading({status: 'ringing', started_at: null, cost_label: '$0.00'}, startMs + 40000);
if (pending.ticking || pending.time !== '00:00' || pending.cost !== '$0.00') process.exit(2);
const later = clockReading({status: 'ringing', started_at: null, cost_label: '$0.00'}, startMs + 42000);
if (later.time !== '00:00') process.exit(3);
const active = clockReading({status: 'active', started_at: startAt, cost_label: '$0.75'}, startMs + 90000);
if (!active.ticking || active.time !== '01:30' || active.cost !== '$0.75') process.exit(4);
const polled = clockReading({status: 'active', started_at: startAt, cost_label: '$0.80'}, startMs + 92000);
if (polled.time !== '01:32') process.exit(5);
"""
    ran = subprocess.run(["node", "-e", script, os.path.join(os.path.dirname(__file__), "..", "web", "connect.js")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_accept_opens_the_session_and_restores_online(client):
    astro_id, token = seed(client, rate=100)
    fund("phone", "wallet_5", "pi_desk")
    headers = auth(token)
    started = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "chat", "question": "When is a good time?",
        "profile": {"name": "Anuprita Khanna", "date": "1990-05-15", "place": {"name": "Delhi"}},
        "idempotency_key": "desk",
    }).json()
    waiting = client.get("/api/connect/astro/live", headers=headers).json()
    assert waiting["online"] is True and waiting["in_session"] is False
    assert waiting["ringing"][0]["seeker_name"] == "Anuprita"
    assert waiting["ringing"][0]["question"] == "When is a good time?"
    accepted = client.post("/api/connect/sessions/" + started["id"] + "/accept", headers=headers).json()
    assert accepted["status"] == "active" and accepted["cost_cents"] == 0
    desk = client.get("/api/connect/astro/live", headers=headers).json()
    assert desk["in_session"] is True and desk["online"] is False
    assert desk["session"]["id"] == accepted["id"]
    posted = client.post("/api/connect/sessions/" + started["id"] + "/messages", headers=headers, json={"sender": "astrologer", "body": "Namaste", "device": ""})
    assert posted.status_code == 200
    saved = posted.json()
    assert saved["ok"] is True and saved["id"] > 0 and saved["sender"] == "astrologer"
    messages = client.get("/api/connect/sessions/" + started["id"] + "/messages", headers=headers).json()["messages"]
    assert messages[0]["body"] == "Namaste" and messages[0]["sender"] == "astrologer" and messages[0]["id"] == saved["id"]
    again = client.get("/api/connect/sessions/" + started["id"] + "/messages?after=" + str(saved["id"]), headers=headers).json()["messages"]
    assert again == []
    ended = client.post("/api/connect/sessions/" + started["id"] + "/end", headers=headers, json={"device": ""}).json()
    assert ended["status"] == "ended" and "net_label" in ended and "fee_label" in ended
    after = client.get("/api/connect/astro/live", headers=headers).json()
    assert after["online"] is True and after["in_session"] is False and after["session"] is None


def test_wallet_intent_is_card_only(client, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("APP_PUBLIC_URL", "https://drishti-ios.vercel.app/")
    captured = {}

    def fake(secret, path, data, idempotency_key=None):
        captured["path"] = path
        captured["data"] = dict(data)
        return {"id": "pi_card", "client_secret": "sec_card"}

    monkeypatch.setattr(connect_stripe, "_form", fake)
    res = client.post("/api/connect/wallet/intent", json={"device": "phone", "product_id": "wallet_5", "idempotency_key": "once"})
    assert res.status_code == 200
    assert res.json()["return_url"] == "https://drishti-ios.vercel.app/#wallet"
    assert captured["path"] == "payment_intents"
    assert captured["data"]["automatic_payment_methods[enabled]"] == "true"
    assert captured["data"]["automatic_payment_methods[allow_redirects]"] == "never"
    assert "payment_method_types[]" not in captured["data"]
    assert "payment_method_types" not in captured["data"]
    client_js = open(os.path.join(os.path.dirname(__file__), "..", "web", "connect.js"), encoding="utf-8").read()
    assert "clientSecret: intent.client_secret" in client_js
    assert "applePay: 'never'" in client_js and "googlePay: 'never'" in client_js


def test_wallet_confirm_shares_the_webhook_id(client, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    seen = {"id": "pi_confirm", "status": "succeeded", "metadata": {"purpose": "wallet", "device": "phone", "product_id": "wallet_5"}}

    def retrieve(secret, intent_id):
        assert secret == "sk_test" and intent_id == seen["id"]
        return dict(seen)

    monkeypatch.setattr(connect_stripe, "retrieve_payment_intent", retrieve)
    first = client.post("/api/connect/wallet/confirm", json={"device": "phone", "payment_intent": "pi_confirm"})
    assert first.status_code == 200 and first.json()["credited"] is True and first.json()["balance_cents"] == 500
    again = client.post("/api/connect/wallet/confirm", json={"device": "phone", "payment_intent": "pi_confirm"})
    assert again.status_code == 200 and again.json()["credited"] is False
    payload = json.dumps({"type": "payment_intent.succeeded", "data": {"object": seen}}).encode()
    stamp = str(int(time.time()))
    sig = hmac.new(b"whsec_test", stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    hooked = client.post("/api/connect/stripe/webhook", content=payload, headers={"Stripe-Signature": f"t={stamp},v1={sig}"})
    assert hooked.status_code == 200 and hooked.json()["credited"] is False
    assert store.wallet_view("phone")["stripe_cents"] == 500
    seen["metadata"] = {"purpose": "wallet", "device": "other", "product_id": "wallet_5"}
    seen["id"] = "pi_other"
    mismatch = client.post("/api/connect/wallet/confirm", json={"device": "phone", "payment_intent": "pi_other"})
    assert mismatch.status_code == 400 and mismatch.json()["detail"] == "payment_invalid"
    seen["status"] = "processing"
    seen["metadata"] = {"purpose": "wallet", "device": "phone", "product_id": "wallet_5"}
    seen["id"] = "pi_wait"
    pending = client.post("/api/connect/wallet/confirm", json={"device": "phone", "payment_intent": "pi_wait"})
    assert pending.status_code == 409 and pending.json()["detail"] == "payment_pending"
    assert store.wallet_view("phone")["stripe_cents"] == 500


def test_apple_question_is_consumed_once(client, monkeypatch):
    def fake(transaction_id):
        return {"transaction_id": transaction_id, "product_id": "acharya_question", "bundle_id": "com.ikshana.drishti"}
    monkeypatch.setattr(connect_storekit, "fetch_transaction", fake)
    monkeypatch.setattr("llm.configured", lambda: False)
    missing = client.post("/api/chat", json={
        "device": "phone", "store": "apple", "transaction_id": "txn-1", "signed_transaction": "",
        "profile": {"name": "Asha", "gender": "female", "date": "1990-05-15", "time": "10:30",
                    "place": {"name": "Delhi", "lat": 28.6, "lon": 77.2, "tz": "Asia/Kolkata"}},
        "messages": [{"role": "user", "content": "When?"}],
    })
    assert b"ai_not_configured" in missing.content
    # The purchase is reserved only after the model is configured. Release nothing yet.
    monkeypatch.setattr("llm.configured", lambda: True)

    class Boom(Exception):
        pass

    async def explode(_msgs):
        raise Boom("no")
        yield "x"

    monkeypatch.setattr("llm.stream_chat", explode)
    failed = client.post("/api/chat", json={
        "device": "phone", "store": "apple", "transaction_id": "txn-2", "signed_transaction": "a.eyJ0cmFuc2FjdGlvbklkIjoidHhuLTIifQ.b",
        "profile": {"name": "Asha", "gender": "female", "date": "1990-05-15", "time": "10:30",
                    "place": {"name": "Delhi", "lat": 28.6, "lon": 77.2, "tz": "Asia/Kolkata"}},
        "messages": [{"role": "user", "content": "When?"}],
    })
    assert failed.status_code == 200 and b"llm_error" in failed.content
    # A failed answer before any token releases the purchase, so the same transaction can be sent again.
    async def one_token(_msgs):
        yield "hello"

    monkeypatch.setattr("llm.stream_chat", one_token)
    ok = client.post("/api/chat", json={
        "device": "phone", "store": "apple", "transaction_id": "txn-2", "signed_transaction": "a.eyJ0cmFuc2FjdGlvbklkIjoidHhuLTIifQ.b",
        "profile": {"name": "Asha", "gender": "female", "date": "1990-05-15", "time": "10:30",
                    "place": {"name": "Delhi", "lat": 28.6, "lon": 77.2, "tz": "Asia/Kolkata"}},
        "messages": [{"role": "user", "content": "When?"}],
    })
    assert b"hello" in ok.content and b'"charged": "apple"' in ok.content
    used = client.post("/api/chat", json={
        "device": "phone", "store": "apple", "transaction_id": "txn-2",
        "profile": {"name": "Asha", "gender": "female", "date": "1990-05-15", "time": "10:30",
                    "place": {"name": "Delhi", "lat": 28.6, "lon": 77.2, "tz": "Asia/Kolkata"}},
        "messages": [{"role": "user", "content": "Again?"}],
    })
    assert b"payment_used" in used.content


def test_wallet_lock_sql_uses_a_row_lock_on_postgres():
    source = open(os.path.join(os.path.dirname(__file__), "..", "server", "connect_store.py"), encoding="utf-8").read()
    assert 'sql += " for update"' in source
    assert "select id from ac_session where id=? for update" in source


def test_rate_bounds_and_subscription_gate(client, monkeypatch):
    low = client.post("/api/connect/apply", json={
        "email": "low@example.com", "password": "correct-horse", "name": "Low Rate",
        "specialties": ["career"], "languages": ["Hindi"], "years": 3, "bio": "Too cheap.", "rate_cents": 29,
    })
    high = client.post("/api/connect/apply", json={
        "email": "high@example.com", "password": "correct-horse", "name": "High Rate",
        "specialties": ["career"], "languages": ["Hindi"], "years": 3, "bio": "Too dear.", "rate_cents": 501,
    })
    assert low.status_code == 400 and high.status_code == 400
    created = client.post("/api/connect/apply", json={
        "email": "edge@example.com", "password": "correct-horse", "name": "Edge Rate",
        "specialties": ["career"], "languages": ["English"], "years": 4, "bio": "Edges.", "rate_cents": 30,
    })
    assert created.status_code == 200
    admin = auth("admin-test-token")
    astro_id = created.json()["id"]
    assert client.post("/api/connect/admin/astrologers/" + astro_id, headers=admin, json={"action": "approve"}).json()["rate_cents"] == 30
    login = client.post("/api/connect/astro/login", json={"email": "edge@example.com", "password": "correct-horse"}).json()
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "now", lambda: base)
    assert client.post("/api/connect/astro/online", headers=auth(login["token"]), json={"online": True}).status_code == 402
    assert client.get("/api/connect/astrologers", params={"device": "phone"}).json()["astrologers"] == []
    assert client.post("/api/connect/astro/trial", headers=auth(login["token"])).status_code == 200
    assert client.post("/api/connect/astro/trial", headers=auth(login["token"])).status_code == 409
    assert client.post("/api/connect/astro/online", headers=auth(login["token"]), json={"online": True}).status_code == 200
    listed = client.get("/api/connect/astrologers", params={"device": "phone"}).json()["astrologers"]
    assert [a["id"] for a in listed] == [astro_id]
    monkeypatch.setattr(store, "now", lambda: base + timedelta(days=91))
    assert client.get("/api/connect/astrologers", params={"device": "phone"}).json()["astrologers"] == []
    monkeypatch.setattr(store, "now", lambda: datetime(2026, 6, 1, tzinfo=timezone.utc))
    store.apply_provider_plan(astro_id, "active", "2030-01-01T00:00:00+00:00", None, "cus_edge", "sub_edge", "month")
    assert client.get("/api/connect/astrologers", params={"device": "phone"}).json()["astrologers"]
    store.apply_provider_plan(astro_id, "past_due", None, None, "cus_edge", "sub_edge", "month")
    assert client.get("/api/connect/astrologers", params={"device": "phone"}).json()["astrologers"]
    monkeypatch.setattr(store, "now", lambda: datetime(2026, 6, 10, tzinfo=timezone.utc))
    assert client.get("/api/connect/astrologers", params={"device": "phone"}).json()["astrologers"] == []
    assert client.post("/api/connect/admin/astrologers/" + astro_id, headers=admin, json={"action": "suspend"}).json()["status"] == "suspended"
    assert client.post("/api/connect/wallet/apple", json={"device": "phone", "product_id": "wallet_5", "transaction_id": "t"}).status_code == 400


def test_free_trial_has_no_billing_account(client):
    created = client.post("/api/connect/apply", json={
        "email": "trial@example.com", "password": "correct-horse", "name": "Trial Ji",
        "specialties": ["career"], "languages": ["Hindi"], "years": 5, "bio": "A trial.", "rate_cents": 100,
    })
    assert created.status_code == 200
    login = client.post("/api/connect/astro/login", json={"email": "trial@example.com", "password": "correct-horse"}).json()
    headers = auth(login["token"])
    assert client.post("/api/connect/astro/trial", headers=headers).status_code == 200
    earned = client.get("/api/connect/astro/earnings", headers=headers).json()
    assert earned["plan_status"] == "trialing"
    assert earned["has_billing_account"] is False
    assert earned["renews_at"]
    portal = client.post("/api/connect/astro/portal", headers=headers)
    assert portal.status_code == 409 and portal.json()["detail"] == "no_billing_account"
    store.set_stripe_customer(created.json()["id"], "cus_trial")
    missing = client.post("/api/connect/astro/portal", headers=headers)
    assert missing.status_code == 503 and missing.json()["detail"] == "stripe_not_configured"
    plan = open(os.path.join(os.path.dirname(__file__), "..", "server", "connect_api.py"), encoding="utf-8").read()
    body = plan.split("def api_plan", 1)[1].split("def api_portal", 1)[0]
    assert 'trial = 0 if info["trial_used"]' in body


def test_admin_reset_password_is_shown_once_and_must_be_changed(client):
    created = client.post("/api/connect/apply", json={
        "email": "reset@example.com", "password": "correct-horse", "name": "Reset Ji",
        "specialties": ["career"], "languages": ["Hindi"], "years": 6, "bio": "Needs a reset.", "rate_cents": 100,
    })
    assert created.status_code == 200
    astro_id = created.json()["id"]
    first = client.post("/api/connect/astro/login", json={"email": "reset@example.com", "password": "correct-horse"}).json()
    assert first["must_change"] is False
    old = auth(first["token"])
    assert client.get("/api/connect/astro/earnings", headers=old).status_code == 200
    admin = auth("admin-test-token")
    listed = client.get("/api/connect/admin/astrologers", headers=admin).json()["astrologers"]
    assert any(a["id"] == astro_id and a["email"] == "reset@example.com" for a in listed)
    reset = client.post("/api/connect/admin/astrologers/" + astro_id + "/reset-password", headers=admin)
    assert reset.status_code == 200
    temporary = reset.json()["temporary_password"]
    assert len(temporary) >= 8
    with db.connect() as con:
        stored = con.execute("select password_hash, must_change from ac_astrologer where id=?", (astro_id,)).fetchone()
    assert temporary not in stored[0] and stored[1] == 1
    assert client.get("/api/connect/astro/earnings", headers=old).status_code == 401
    assert client.post("/api/connect/astro/login", json={"email": "reset@example.com", "password": "correct-horse"}).status_code == 401
    second = client.post("/api/connect/astro/login", json={"email": "reset@example.com", "password": temporary}).json()
    assert second["must_change"] is True
    fresh = auth(second["token"])
    blocked = client.get("/api/connect/astro/earnings", headers=fresh)
    assert blocked.status_code == 409 and blocked.json()["detail"] == "must_change"
    short = client.post("/api/connect/astro/password", headers=fresh, json={"password": "short"})
    assert short.status_code == 400
    changed = client.post("/api/connect/astro/password", headers=fresh, json={"password": "new-secret"})
    assert changed.status_code == 200
    assert client.post("/api/connect/astro/login", json={"email": "reset@example.com", "password": temporary}).status_code == 401
    again = client.post("/api/connect/astro/login", json={"email": "reset@example.com", "password": "new-secret"}).json()
    assert again["must_change"] is False
    assert client.get("/api/connect/astro/earnings", headers=auth(again["token"])).status_code == 200
    gone = client.get("/api/connect/admin/astrologers", headers=admin).json()["astrologers"]
    assert temporary not in str(gone)


def test_must_change_column_is_added_to_an_older_table(tmp_path, monkeypatch):
    monkeypatch.setenv("DRISHTI_DB", str(tmp_path / "old.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import sqlite3
    raw = sqlite3.connect(tmp_path / "old.db")
    raw.execute("create table ac_astrologer(id text primary key, email text)")
    raw.commit()
    raw.close()
    with db.connect() as con:
        store.ensure(con)
        cols = [r[1] for r in con.execute("pragma table_info(ac_astrologer)").fetchall()]
    assert "must_change" in cols


def test_customer_sees_the_astrologer_reply_once(client):
    astro_id, token = seed(client, rate=100)
    fund("phone", "wallet_5", "pi_reply")
    headers = auth(token)
    started = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "chat", "question": "When is a good time?",
        "profile": {"name": "Anuprita Khanna", "date": "1990-05-15", "place": {"name": "Delhi"}},
        "idempotency_key": "reply-once",
    }).json()
    assert started["question"] == "When is a good time?"
    accepted = client.post("/api/connect/sessions/" + started["id"] + "/accept", headers=headers).json()
    assert accepted["question"] == "When is a good time?"
    asked = client.post("/api/connect/sessions/" + started["id"] + "/messages", json={
        "sender": "user", "body": "Is next spring better?", "device": "phone",
    })
    assert asked.status_code == 200
    reply = client.post("/api/connect/sessions/" + started["id"] + "/messages", headers=headers, json={
        "sender": "astrologer", "body": "Yes, after Jupiter moves.", "device": "",
    })
    assert reply.status_code == 200 and reply.json()["id"] > asked.json()["id"]
    listed = client.get("/api/connect/sessions/" + started["id"] + "/messages", params={"device": "phone", "after": 0})
    assert listed.headers["cache-control"] == "private, no-store"
    messages = listed.json()["messages"]
    assert [(m["sender"], m["body"]) for m in messages] == [
        ("user", "Is next spring better?"),
        ("astrologer", "Yes, after Jupiter moves."),
    ]
    assert sum(1 for m in messages if m["id"] == reply.json()["id"]) == 1
    again = client.get("/api/connect/sessions/" + started["id"] + "/messages", params={"device": "phone", "after": 0}).json()["messages"]
    assert [m["id"] for m in again] == [m["id"] for m in messages]
    desk = client.get("/api/connect/astro/live", headers=headers).json()
    assert desk["session"]["question"] == "When is a good time?"


def test_chat_keeps_one_bubble_and_one_poll():
    root = os.path.join(os.path.dirname(__file__), "..")
    connect = open(os.path.join(root, "web", "connect.js"), encoding="utf-8").read()
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    assert connect.count("setInterval(() => beat()") == 1
    assert "clearInterval(timer)" in connect and "if (!live || beating) return" in connect
    assert "if (!text || !live || sending) return" in connect and "lockSend(true)" in connect
    assert 'id="connectSend"' in connect
    assert "function mergeById" in connect and "function showMessages" in connect and "function renderThread" in connect
    assert "data-pending" in connect
    assert "className = 'bubble ' + (mine ? 'me' : 'them')" in connect
    assert "who.className = 'who'" in connect
    assert "addMsg" not in connect and "claimMessage" not in connect
    assert "after=0" in connect and "keepLive" in html
    assert "cache: 'no-store'" in connect
    assert "sender=user" not in connect and "sender=astrologer" not in connect
    assert "function questionForSession" in connect
    assert "DrishtiConnect.stop" in html
    assert ".connect-log .bubble.me{align-self:flex-end" in html
    assert ".connect-log .bubble.them{align-self:flex-start" in html
    assert ".connect-log .bubble.me p{background:var(--primary-tint)}" in html
    assert ".connect-log .bubble.them p{background:var(--sunk)}" in html
    assert ".connect-log .who{" in html
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('function mergeById');
const end = src.indexOf('function showMessages');
if (start < 0 || end < 0) process.exit(2);
eval(src.slice(start, end));
const ids = rows => rows.map(m => m.id + ':' + m.sender + ':' + m.body).join('|');
let thread = mergeById([], [{id: 'tmp-1', sender: 'user', body: 'Hi'}]);
thread = mergeById(thread, [
  {id: 7, sender: 'user', body: 'Hi'},
  {id: 8, sender: 'astrologer', body: 'Hello'},
]);
if (ids(thread) !== '7:user:Hi|8:astrologer:Hello') process.exit(3);
thread = mergeById(thread, [{id: 7, sender: 'user', body: 'Hi'}]);
if (ids(thread) !== '7:user:Hi|8:astrologer:Hello') process.exit(4);
thread = mergeById(thread, [
  {id: 7, sender: 'user', body: 'Hi'},
  {id: 8, sender: 'astrologer', body: 'Hello'},
]);
if (ids(thread) !== '7:user:Hi|8:astrologer:Hello') process.exit(5);
thread = mergeById(thread, [{id: 'tmp-2', sender: 'astrologer', body: 'Again'}]);
if (ids(thread) !== '7:user:Hi|8:astrologer:Hello|tmp-2:astrologer:Again') process.exit(6);
thread = mergeById(thread, [{id: 9, sender: 'user', body: 'Hi'}]);
if (ids(thread) !== '7:user:Hi|8:astrologer:Hello|9:user:Hi|tmp-2:astrologer:Again') process.exit(7);
thread = mergeById(thread, [{id: 10, sender: 'astrologer', body: 'Again'}]);
if (ids(thread) !== '7:user:Hi|8:astrologer:Hello|9:user:Hi|10:astrologer:Again') process.exit(8);
const qStart = src.indexOf('function questionForSession');
const qEnd = src.indexOf('function lastQuestion');
eval(src.slice(qStart, qEnd));
if (questionForSession('  Career? ', '', []) !== 'Career?') process.exit(11);
if (questionForSession('', '  From match  ', [{role: 'user', content: 'old'}]) !== 'From match') process.exit(12);
if (questionForSession('', '', [{role: 'assistant', content: 'x'}, {role: 'user', content: 'When?'}]) !== 'When?') process.exit(13);
if (questionForSession('', '', [{role: 'user', content: 'first'}, {role: 'user', text: 'second'}]) !== 'second') process.exit(14);
if (questionForSession('', '', []) !== '') process.exit(15);
"""
    ran = subprocess.run(["node", "-e", script, os.path.join(root, "web", "connect.js")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_admin_buttons_match_the_astrologer_status():
    root = os.path.join(os.path.dirname(__file__), "..")
    connect = open(os.path.join(root, "web", "connect.js"), encoding="utf-8").read()
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    assert "function adminActions" in connect and "adminActions(a.status)" in connect
    assert "data-status=" in connect and "function adminDone" in connect
    assert "if (window.toast) toast(tr(note[0], note[1]))" in connect
    assert "showErr(err)" in connect
    assert ".admin-acts" in html and ".tag.bad" in html
    assert 'data-act="approve">${tr(\'Approve\'' not in connect
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('function adminActions');
const end = src.indexOf('async function renderAdmin');
if (start < 0 || end < 0) process.exit(2);
eval(src.slice(start, end));
const acts = status => adminActions(status).map(item => item.act + ':' + item.label[0]).join(',');
if (acts('pending') !== 'approve:Approve,remove:Reject') process.exit(3);
if (acts('approved') !== 'suspend:Suspend,remove:Remove') process.exit(4);
if (acts('suspended') !== 'approve:Reinstate,remove:Remove') process.exit(5);
if (acts('removed') !== '' || acts('') !== '') process.exit(6);
if (adminDone('approve', 'pending').join('|') !== 'Approved|स्वीकृत') process.exit(7);
if (adminDone('approve', 'suspended').join('|') !== 'Reinstated|बहाल किया गया') process.exit(8);
if (adminDone('suspend', 'approved')[0] !== 'Suspended') process.exit(9);
if (adminDone('remove', 'pending')[0] !== 'Rejected') process.exit(10);
if (adminDone('remove', 'approved')[0] !== 'Removed') process.exit(11);
if (adminPill('pending') !== 'tag') process.exit(12);
if (adminPill('approved') !== 'tag good') process.exit(13);
if (adminPill('suspended') !== 'tag warn') process.exit(14);
if (adminPill('removed') !== 'tag bad') process.exit(15);
"""
    ran = subprocess.run(["node", "-e", script, os.path.join(root, "web", "connect.js")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_stripe_failures_are_502_and_do_not_save_a_missing_account(client, monkeypatch, caplog):
    astro_id, token = seed(client)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_SECRETKEY")
    monkeypatch.setenv("APP_PUBLIC_URL", "https://drishti-ios.vercel.app")
    headers = auth(token)

    class Res:
        status_code = 400

        def json(self):
            return {"error": {
                "type": "invalid_request_error",
                "message": "You can only create new accounts if you've signed up for Connect sk_test_SECRETKEY",
            }}

    def post(url, data=None, auth=None, headers=None, timeout=None, **kwargs):
        assert auth == ("sk_test_SECRETKEY", "")
        return Res()

    monkeypatch.setattr(connect_stripe.httpx, "post", post)
    with caplog.at_level("WARNING", logger="drishti.stripe"):
        with pytest.raises(connect_stripe.StripeError) as caught:
            connect_stripe.create_express_account("sk_test_SECRETKEY", "meera@example.com", "acct-1")
    assert connect_stripe.payout_code(caught.value) == "connect_not_enabled"
    assert "signed up for Connect" in caplog.text
    assert "sk_test_SECRETKEY" not in caplog.text
    assert connect_stripe.payout_code(connect_stripe.StripeError(
        "stripe_rejected", message="The Connect platform is not enabled")) == "connect_not_enabled"
    assert connect_stripe.payout_code(connect_stripe.StripeError(
        "stripe_rejected", stripe_code="rate_limit", message="Too many requests")) == "payout_setup_failed"

    def refuse_account(secret, email, key):
        raise connect_stripe.StripeError(
            "stripe_rejected", message="You can only create new accounts if you've signed up for Connect")

    monkeypatch.setattr(connect_stripe, "create_express_account", refuse_account)
    refused = client.post("/api/connect/astro/stripe", headers=headers)
    assert refused.status_code == 502 and refused.json()["detail"] == "connect_not_enabled"
    assert store.astrologer_private(astro_id)["stripe_account"] == ""

    def other_failure(secret, email, key):
        raise connect_stripe.StripeError("stripe_rejected", stripe_code="rate_limit", message="Too many requests")

    monkeypatch.setattr(connect_stripe, "create_express_account", other_failure)
    generic = client.post("/api/connect/astro/stripe", headers=headers)
    assert generic.status_code == 502 and generic.json()["detail"] == "payout_setup_failed"
    assert store.astrologer_private(astro_id)["stripe_account"] == ""

    creates = []

    def created(secret, email, key):
        creates.append(key)
        return {"id": "acct_ok"}

    def link_fails(secret, account, refresh, back):
        raise connect_stripe.StripeError("stripe_rejected", message="link failed")

    monkeypatch.setattr(connect_stripe, "create_express_account", created)
    monkeypatch.setattr(connect_stripe, "create_account_link", link_fails)
    linked = client.post("/api/connect/astro/stripe", headers=headers)
    assert linked.status_code == 502 and linked.json()["detail"] == "payout_setup_failed"
    assert store.astrologer_private(astro_id)["stripe_account"] == "acct_ok"

    def linked_ok(secret, account, refresh, back):
        assert account == "acct_ok"
        return {"url": "https://connect.stripe.com/setup/ok"}

    monkeypatch.setattr(connect_stripe, "create_account_link", linked_ok)
    ready = client.post("/api/connect/astro/stripe", headers=headers)
    assert ready.status_code == 200 and ready.json()["url"] == "https://connect.stripe.com/setup/ok"
    assert creates == ["acct-astro-v2-" + astro_id]

    def bad_intent(secret, cents, metadata, key):
        raise connect_stripe.StripeError("stripe_rejected", message="card error")

    monkeypatch.setattr(connect_stripe, "create_payment_intent", bad_intent)
    intent = client.post("/api/connect/wallet/intent", json={"device": "phone", "product_id": "wallet_5", "idempotency_key": "once"})
    assert intent.status_code == 502 and intent.json()["detail"] == "stripe_rejected"

    def bad_retrieve(secret, intent_id):
        raise connect_stripe.StripeError("stripe_unavailable")

    monkeypatch.setattr(connect_stripe, "retrieve_payment_intent", bad_retrieve)
    confirm = client.post("/api/connect/wallet/confirm", json={"device": "phone", "payment_intent": "pi_missing"})
    assert confirm.status_code == 502 and confirm.json()["detail"] == "stripe_unavailable"

    monkeypatch.setenv("STRIPE_PRICE_PROVIDER_MONTHLY", "price_month")

    def bad_customer(secret, email, astro):
        raise connect_stripe.StripeError("stripe_rejected", message="customer failed")

    monkeypatch.setattr(connect_stripe, "create_customer", bad_customer)
    plan = client.post("/api/connect/astro/plan", headers=headers, json={"interval": "month"})
    assert plan.status_code == 502 and plan.json()["detail"] == "stripe_rejected"
    assert store.astrologer_private(astro_id)["stripe_customer"] == ""

    store.set_stripe_customer(astro_id, "cus_saved")

    def bad_portal(secret, customer, return_url):
        raise connect_stripe.StripeError("stripe_rejected", message="portal failed")

    monkeypatch.setattr(connect_stripe, "create_portal", bad_portal)
    portal = client.post("/api/connect/astro/portal", headers=headers)
    assert portal.status_code == 502 and portal.json()["detail"] == "stripe_rejected"
    connect = open(os.path.join(os.path.dirname(__file__), "..", "web", "connect.js"), encoding="utf-8").read()
    assert "connect_not_enabled" in connect and "payout_setup_failed" in connect
    assert "Payouts are not switched on yet. Please try again later." in connect
    assert "भुगतान अभी चालू नहीं हैं। बाद में फिर कोशिश करें।" in connect
    assert "Your bank and ID details go straight to Stripe. Drishti never sees or stores them." in connect
    assert "Finish payout setup" in connect and "भुगतान सेटअप पूरा करें" in connect


def test_connected_account_is_v2_with_stripe_losses(monkeypatch):
    calls = []

    class Res:
        def __init__(self, payload):
            self.status_code = 200
            self.payload = payload

        def json(self):
            return self.payload

    def post(url, data=None, json=None, auth=None, headers=None, timeout=None):
        calls.append({"url": url, "data": data, "json": json, "headers": dict(headers or {})})
        if url.endswith("/v2/core/accounts"):
            return Res({"id": "acct_v2", "object": "v2.core.account"})
        if url.endswith("/v2/core/account_links"):
            return Res({"url": "https://connect.stripe.com/setup/v2", "object": "v2.core.account_link"})
        if url.endswith("/v1/transfers"):
            return Res({"id": "tr_1", "object": "transfer"})
        raise AssertionError(url)

    monkeypatch.setattr(connect_stripe.httpx, "post", post)
    created = connect_stripe.create_express_account("sk_test_x", "a@b.c", "acct-astro-v2-astro_1")
    assert created["id"] == "acct_v2"
    account = calls[0]
    assert account["url"] == "https://api.stripe.com/v2/core/accounts"
    assert account["data"] is None
    body = account["json"]
    assert "type" not in body
    assert body["contact_email"] == "a@b.c"
    assert body["dashboard"] == "express"
    assert body["identity"] == {"country": "US"}
    assert body["defaults"]["responsibilities"] == {
        "fees_collector": "application",
        "losses_collector": "stripe",
    }
    assert body["configuration"]["recipient"]["capabilities"]["stripe_balance"]["stripe_transfers"]["requested"] is True
    assert body["configuration"]["merchant"]["capabilities"]["card_payments"]["requested"] is True
    assert account["headers"]["Idempotency-Key"] == "acct-astro-v2-astro_1"
    assert account["headers"]["Stripe-Version"] == "2026-10-28.preview"

    link = connect_stripe.create_account_link("sk_test_x", "acct_v2", "https://app.example/#astro", "https://app.example/#astro")
    assert link["url"] == "https://connect.stripe.com/setup/v2"
    onboarding = calls[1]["json"]["use_case"]
    assert calls[1]["url"] == "https://api.stripe.com/v2/core/account_links"
    assert onboarding["type"] == "account_onboarding"
    assert "configurations" not in onboarding["account_onboarding"]
    assert onboarding["account_onboarding"]["collection_options"] == {"fields": "currently_due"}
    assert onboarding["account_onboarding"]["refresh_url"] == "https://app.example/#astro"
    assert onboarding["account_onboarding"]["return_url"] == "https://app.example/#astro"
    assert calls[1]["data"] is None
    assert calls[1]["headers"]["Content-Type"] == "application/json"
    assert calls[1]["headers"]["Stripe-Version"] == "2026-10-28.preview"
    assert connect_stripe.transfers_active({}) is False
    assert connect_stripe.transfers_active({
        "configuration": {"recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"status": "pending"}}}}},
    }) is False
    assert connect_stripe.transfers_active({
        "configuration": {"recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"status": "active"}}}}},
    }) is True

    transfer = connect_stripe.create_transfer("sk_test_x", 2500, "acct_v2", "transfer-astro_1")
    assert transfer["id"] == "tr_1"
    assert calls[2]["url"] == "https://api.stripe.com/v1/transfers"
    assert calls[2]["data"] == {"amount": "2500", "currency": "usd", "destination": "acct_v2"}
    assert calls[2]["headers"]["Idempotency-Key"] == "transfer-astro_1"
    assert "Stripe-Version" not in calls[2]["headers"]


def test_saved_account_is_not_ready_until_transfers_are_active(client, monkeypatch):
    astro_id, token = seed(client)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("CRON_SECRET", "cron-secret")
    store.set_stripe_account(astro_id, "acct_saved")
    state = {"status": "pending"}

    def retrieve(secret, account_id):
        assert secret == "sk_test" and account_id == "acct_saved"
        account = {"configuration": {"recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"status": state["status"]}}}}}}
        if state.get("due"):
            account["requirements"] = {"summary": {"minimum_deadline": {"status": state["due"]}}}
        return account

    monkeypatch.setattr(connect_stripe, "retrieve_account", retrieve)
    waiting = client.get("/api/connect/astro/earnings", headers=auth(token)).json()
    assert waiting["has_payout_account"] is True and waiting["payouts_ready"] is False
    state["status"] = "active"
    state["due"] = "past_due"
    blocked = client.get("/api/connect/astro/earnings", headers=auth(token)).json()
    assert blocked["payouts_ready"] is False
    monkeypatch.setenv("APP_PUBLIC_URL", "https://drishti-ios.vercel.app")
    created = []
    monkeypatch.setattr(connect_stripe, "create_express_account", lambda *a, **k: created.append("account"))
    monkeypatch.setattr(connect_stripe, "create_account_link", lambda *a, **k: {"url": "https://connect.stripe.com/setup/fresh"})
    state["due"] = "currently_due"
    state["status"] = "pending"
    fresh = client.post("/api/connect/astro/stripe", headers=auth(token))
    assert fresh.status_code == 200 and fresh.json()["url"] == "https://connect.stripe.com/setup/fresh" and created == []
    state["due"] = ""
    state["status"] = "active"
    ready = client.get("/api/connect/astro/earnings", headers=auth(token)).json()
    assert ready["payouts_ready"] is True

    sent = []
    monkeypatch.setattr(store, "payout_candidates", lambda moment=None: [
        {"astrologer_id": astro_id, "stripe_account": "acct_saved", "amount_cents": 500},
    ])
    monkeypatch.setattr(connect_stripe, "create_transfer", lambda *args: sent.append(args) or {"id": "tr_ready"})
    state["status"] = "pending"
    held = client.get("/api/cron/connect-payouts", headers={"Authorization": "Bearer cron-secret"})
    assert held.status_code == 200 and held.json()["payouts"] == [] and sent == []
    state["status"] = "active"
    paid = client.get("/api/cron/connect-payouts", headers={"Authorization": "Bearer cron-secret"})
    assert paid.status_code == 200 and len(paid.json()["payouts"]) == 1 and len(sent) == 1


def test_approved_astrologer_can_change_rate_inside_the_bounds(client):
    astro_id, token = seed(client)
    headers = auth(token)
    low = client.post("/api/connect/astro/rate", headers=headers, json={"rate_cents": 29})
    high = client.post("/api/connect/astro/rate", headers=headers, json={"rate_cents": 501})
    assert low.status_code == 400 and high.status_code == 400
    saved = client.post("/api/connect/astro/rate", headers=headers, json={"rate_cents": 150})
    assert saved.status_code == 200 and saved.json()["rate_cents"] == 150
    assert client.get("/api/connect/astro/me", headers=headers).json()["status"] == "approved"
    connect = open(os.path.join(os.path.dirname(__file__), "..", "web", "connect.js"), encoding="utf-8").read()
    html = open(os.path.join(os.path.dirname(__file__), "..", "web", "index.html"), encoding="utf-8").read()
    assert "You're an approved astrologer" in connect and "Switch to Astrologer mode" in connect
    assert "Switch to User mode" in connect and "Astrologer mode" in connect
    assert "function renderAstroMode" in connect and "function choosePayoutMethod" in connect
    assert 'data-payout-method="stripe"' in connect
    assert "Your bank and ID details go straight to Stripe. Drishti never sees or stores them." in connect
    assert "astro-mode" in html and "#4B4789" in html
    assert 'id="astroModeSlot"' in html


def test_paypal_email_is_private_and_the_weekly_payout_is_sent_once(client, monkeypatch):
    astro_id, token = seed(client)
    headers = auth(token)
    assert client.post("/api/connect/astro/payout", json={
        "method": "paypal", "paypal_email": "n@gmail.com", "confirm_email": "n@gmail.com",
    }).status_code == 401
    mismatch = client.post("/api/connect/astro/payout", headers=headers, json={
        "method": "paypal", "paypal_email": "n@gmail.com", "confirm_email": "other@gmail.com",
    })
    assert mismatch.status_code == 400 and mismatch.json()["detail"] == "email_mismatch"
    bad = client.post("/api/connect/astro/payout", headers=headers, json={
        "method": "paypal", "paypal_email": "not-an-email", "confirm_email": "not-an-email",
    })
    assert bad.status_code == 400 and bad.json()["detail"] == "bad_email"
    saved = client.post("/api/connect/astro/payout", headers=headers, json={
        "method": "paypal", "paypal_email": "N@gmail.com", "confirm_email": "n@gmail.com",
    })
    assert saved.status_code == 200 and saved.json()["payout_mask"] == "n•••@gmail.com"
    earned = client.get("/api/connect/astro/earnings", headers=headers).json()
    assert earned["payouts_ready"] is True and earned["payout_method"] == "paypal"
    assert earned["payout_mask"] == "n•••@gmail.com" and "n@gmail.com" not in str(earned)
    with db.connect() as con:
        store.ensure(con)
        con.execute(
            """insert into ac_earning(session_id, astrologer_id, amount_cents, platform_cents, fee_cents, available_at)
               values(?,?,?,?,?,?)""",
            ("ses_pay", astro_id, 500, 26, 0, "2020-01-01T00:00:00+00:00"))
    monkeypatch.setenv("CRON_SECRET", "cron-secret")
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "client-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "super-secret")
    calls = []

    class Res:
        def __init__(self, payload, status=200):
            self.status_code = status
            self.payload = payload

        def json(self):
            return self.payload

    def post(url, data=None, json=None, auth=None, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/v1/oauth2/token"):
            assert auth == ("client-id", "super-secret")
            return Res({"access_token": "tok"})
        assert headers["Authorization"] == "Bearer tok"
        assert "super-secret" not in str(json)
        item = json["items"][0]
        assert item["recipient_type"] == "EMAIL"
        assert item["receiver"] == "n@gmail.com"
        assert item["amount"] == {"value": "5.00", "currency": "USD"}
        assert item["note"] == "Drishti weekly earnings"
        assert json["sender_batch_header"]["sender_batch_id"] == store.payout_batch_id(astro_id)
        return Res({"batch_header": {"payout_batch_id": "BATCH1", "batch_status": "PENDING"}})

    monkeypatch.setattr(connect_paypal.httpx, "post", post)
    first = client.get("/api/cron/connect-payouts", headers={"Authorization": "Bearer cron-secret"})
    assert first.status_code == 200 and len(first.json()["payouts"]) == 1
    assert "super-secret" not in str(first.json())
    again = client.get("/api/cron/connect-payouts", headers={"Authorization": "Bearer cron-secret"})
    assert again.json()["payouts"] == []
    assert sum(1 for url in calls if url.endswith("/v1/payments/payouts")) == 1
    with db.connect() as con:
        store.ensure(con)
        con.execute("update ac_earning set payout_id=null where session_id=?", ("ses_pay",))
        con.execute("delete from ac_payout where astrologer_id=?", (astro_id,))

    def duplicate(url, data=None, json=None, auth=None, headers=None, timeout=None):
        if url.endswith("/v1/oauth2/token"):
            return Res({"access_token": "tok"})
        return Res({"details": [{"field": "SENDER_BATCH_ID", "issue": "Batch with given sender_batch_id already exists"}]}, 400)

    monkeypatch.setattr(connect_paypal.httpx, "post", duplicate)
    replay = client.get("/api/cron/connect-payouts", headers={"Authorization": "Bearer cron-secret"})
    assert replay.status_code == 200 and replay.json()["payouts"][0]["provider"] == "paypal"
    third = client.get("/api/cron/connect-payouts", headers={"Authorization": "Bearer cron-secret"})
    assert third.json()["payouts"] == []
    connect = open(os.path.join(os.path.dirname(__file__), "..", "web", "connect.js"), encoding="utf-8").read()
    assert "How do you want to get paid?" in connect and "Coming soon" in connect
    assert "id=\"pickRazorpay\" disabled" in connect
    assert "Save PayPal" in connect


def test_voice_joins_only_after_the_call_is_active():
    connect = open(os.path.join(os.path.dirname(__file__), "..", "web", "connect.js"), encoding="utf-8").read()
    opened = connect.split("async function openCall", 1)[1].split("async function finish", 1)[0]
    assert "call-token" not in opened and "joinVoice()" in opened
    assert "view.mode === 'call') joinVoice()" in connect
    assert "function joinVoice" in connect and "call_not_ready" in connect
    assert "livekit-client@2.9.0" in connect and "room.connect(info.url, info.token)" in connect
    assert "setMicrophoneEnabled(true)" in connect and "startAudio" in connect
    assert "getUserMedia({audio: true})" in connect and "publishTrack(media, {source: 'microphone'})" in connect
    assert "for (let attempt = 0; attempt < 3; attempt++)" in connect
    assert "setVoiceNote(voiceErrorText(err))" in connect.split("await publishMic(room);", 1)[1].split("if (room.canPlaybackAudio", 1)[0]
    assert "live.role === 'astrologer'" in connect and "function localMicLabel" in connect
    assert "const enableMic = wasMuted" in connect
    assert "Tap to enable audio" in connect and "आवाज़ चालू करने के लिए टैप करें" in connect
    assert "playsinline" in connect and "connectRemoteAudio" in connect
    assert "The microphone is blocked." in connect
    assert "The voice room could not be reached." in connect
    assert "Voice is not set up on the server yet." in connect
    plist = open(os.path.join(os.path.dirname(__file__), "..", "ios", "Info.plist.additions.xml"), encoding="utf-8").read()
    assert "NSMicrophoneUsageDescription" in plist
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('function voiceErrorText');
const end = src.indexOf('function attachRemoteAudio');
let S = {lang: 'en'};
const tr = (en) => en;
eval(src.slice(start, end));
if (voiceErrorText({code: 'call_unavailable'}).indexOf('not set up') < 0) process.exit(2);
if (voiceErrorText({name: 'NotAllowedError'}).indexOf('microphone is blocked') < 0) process.exit(3);
if (voiceErrorText({code: 'call_not_ready'}).indexOf('Waiting') < 0) process.exit(4);
if (voiceErrorText(new Error('failed')).indexOf('could not be reached') < 0) process.exit(5);
if (voiceErrorText(new Error('setMutedState failed due to error')).indexOf('microphone is blocked') < 0) process.exit(6);
const micStart = src.indexOf('function localMicLabel');
const micEnd = src.indexOf('function micDenied');
eval(src.slice(micStart, micEnd));
const pubs = {forEach(fn) { fn({source: 'microphone', isMuted: false, track: {isMuted: false, mediaStreamTrack: {readyState: 'live', enabled: true}}}); }};
if (localMicLabel({localParticipant: {trackPublications: pubs}}) !== 'published · track yes') process.exit(7);
const mutedPubs = {forEach(fn) { fn({source: 'microphone', isMuted: true, track: {mediaStreamTrack: {readyState: 'live', enabled: false}}}); }};
if (localMicLabel({localParticipant: {trackPublications: mutedPubs}}) !== 'muted · track yes') process.exit(8);
if (localMicLabel({localParticipant: {trackPublications: {forEach() {}}}}) !== 'unpublished · track no') process.exit(9);
"""
    ran = subprocess.run(["node", "-e", script, os.path.join(os.path.dirname(__file__), "..", "web", "connect.js")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_next_payout_is_the_monday_on_or_after_available():
    store_src = open(os.path.join(os.path.dirname(__file__), "..", "server", "connect_store.py"), encoding="utf-8").read()
    assert "timedelta(days=7)" in store_src
    assert "PAYOUT_HOLD_DAYS" not in store_src
    connect = open(os.path.join(os.path.dirname(__file__), "..", "web", "connect.js"), encoding="utf-8").read()
    assert "payoutMonday(money.next_payout_at)" in connect
    script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('function payoutMonday');
const end = src.indexOf('function todayNet');
eval(src.slice(start, end));
let S = {lang: 'en'};
if (payoutMonday('2026-11-25T12:00:00+00:00') !== 'Monday, Nov 30') process.exit(2);
if (payoutMonday('2026-11-30T00:00:00+00:00') !== 'Monday, Nov 30') process.exit(3);
S = {lang: 'hi'};
if (payoutMonday('2026-11-25T12:00:00+00:00') !== 'सोमवार, 30 नवंबर') process.exit(4);
"""
    ran = subprocess.run(["node", "-e", script, os.path.join(os.path.dirname(__file__), "..", "web", "connect.js")], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr or ran.stdout


def test_stripe_signature_helper_rejects_a_stale_event():
    payload = b"{}"
    stamp = str(int(time.time()) - 1000)
    sig = hmac.new(b"sec", stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    with pytest.raises(connect_stripe.StripeError):
        connect_stripe.verify_event(payload, f"t={stamp},v1={sig}", "sec")


def test_delete_refunds_newest_card_first_and_still_deletes_when_stripe_fails(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 11, 19, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "now", lambda: base)
    astro_id, token = seed(client, rate=100)
    fund("phone", "wallet_10", "pi_old")
    fund("phone", "wallet_5", "pi_new")
    with db.connect() as con:
        store.ensure(con)
        con.execute("update ac_payment set created=? where id=?", ("2026-11-19T00:00:02+00:00", "pi_new"))
    started = client.post("/api/connect/sessions", json={
        "device": "phone", "astrologer_id": astro_id, "mode": "chat", "idempotency_key": "del-live",
    }).json()
    blocked = client.post("/api/connect/account/delete", json={"device": "phone"})
    assert blocked.status_code == 409 and blocked.json()["detail"] == "session_active"
    assert store.wallet_view("phone")["balance_cents"] == 1000
    client.post("/api/connect/sessions/" + started["id"] + "/accept", headers=auth(token))
    client.post("/api/connect/sessions/" + started["id"] + "/messages", json={
        "device": "phone", "sender": "user", "body": "hello there",
    })
    monkeypatch.setattr(store, "now", lambda: base + timedelta(seconds=120))
    client.post("/api/connect/sessions/" + started["id"] + "/end", json={"device": "phone"})
    calls = []

    def refund(secret, intent, cents, key):
        calls.append((intent, cents))
        if intent == "pi_old":
            raise connect_stripe.StripeError("stripe_rejected")
        return {"id": "re_new"}

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_delete")
    monkeypatch.setattr(connect_stripe, "refund_payment", refund)
    done = client.post("/api/connect/account/delete", json={"device": "phone"})
    assert done.status_code == 200
    body = done.json()
    assert calls[0] == ("pi_new", 500)
    assert calls[1] == ("pi_old", 800)
    assert body["refunded_cents"] == 500 and body["refund_failed_cents"] == 800
    assert store.wallet_view("phone")["balance_cents"] == 0
    with db.connect() as con:
        store.ensure(con)
        assert con.execute("select count(*) from ac_session where device=?", ("phone",)).fetchone()[0] == 0
        assert con.execute("select count(*) from ac_message where body=?", ("hello there",)).fetchone()[0] == 0
        pay = con.execute("select device, refunded_cents from ac_payment where id='pi_new'").fetchone()
        assert pay[0] == "" and int(pay[1]) == 500
        accounts = [row[0] for row in con.execute("select distinct account from ac_ledger where reason like 'wallet%'").fetchall()]
        assert "wallet:phone" not in accounts and "wallet:removed" in accounts


def test_delete_astrologer_unlist_forfeit_and_cancel(client, monkeypatch):
    astro_id, token = seed(client, rate=100)
    cancelled = []
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_delete")
    monkeypatch.setattr(connect_stripe, "cancel_subscription", lambda secret, sub: cancelled.append(sub) or {"id": sub})
    with db.connect() as con:
        store.ensure(con)
        con.execute("update ac_astrologer set stripe_subscription=? where id=?", ("sub_plan", astro_id))
        con.execute(
            """insert into ac_earning(session_id, astrologer_id, amount_cents, platform_cents, fee_cents, available_at)
               values(?,?,?,?,?,?)""",
            ("ses_small", astro_id, 50, 3, 0, "2026-11-01T00:00:00+00:00"))
    gone = client.post("/api/connect/astro/delete", headers=auth(token))
    assert gone.status_code == 200 and gone.json()["forfeited"] is True
    assert cancelled == ["sub_plan"]
    assert client.get("/api/connect/astro/me", headers=auth(token)).status_code == 401
    assert client.post("/api/connect/astro/login", json={"email": "meera@example.com", "password": "correct-horse"}).status_code == 401
    listed = client.get("/api/connect/astrologers", params={"device": "phone"}).json()["astrologers"]
    assert all(item["id"] != astro_id for item in listed)
    with db.connect() as con:
        store.ensure(con)
        status, photo = con.execute("select status, photo from ac_astrologer where id=?", (astro_id,)).fetchone()
        assert status == "deleted" and photo == ""
        assert con.execute("select payout_id from ac_earning where session_id='ses_small'").fetchone()[0] == "forfeited"
    connect = open(os.path.join(os.path.dirname(__file__), "..", "web", "connect.js"), encoding="utf-8").read()
    html = open(os.path.join(os.path.dirname(__file__), "..", "web", "index.html"), encoding="utf-8").read()
    terms = open(os.path.join(os.path.dirname(__file__), "..", "web", "terms.html"), encoding="utf-8").read()
    assert "Type DELETE" in connect and "5–10 business days" in connect
    assert 'id="deleteAccount"' in html and "terms.html" in html
    assert 'id="regAgree"' in connect and 'id="connectReport"' in connect.split("async function openCall", 1)[1].split("async function finish", 1)[0]
    assert "Harassment" in connect and "Or describe what happened" in connect
    assert "Ikshana Solutions LLC" in terms and "273 Lewis Fisher Dr" in terms and "New Jersey" in terms
    assert "independent providers" in terms and "5%" in terms


def _arm_demo(monkeypatch):
    import secrets
    email = "review-" + secrets.token_hex(4) + "@example.com"
    password = secrets.token_hex(16)
    code = secrets.token_hex(8)
    monkeypatch.setenv("DEMO_ASTRO_EMAIL", email)
    monkeypatch.setenv("DEMO_ASTRO_PASSWORD", password)
    monkeypatch.setenv("DEMO_REVIEW_CODE", code)
    monkeypatch.delenv("DEMO_REVIEW", raising=False)
    return email, password, code


def test_demo_is_hidden_until_a_device_opts_in(client, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import wave
    astro_id, _token = seed(client)
    fund("phone-real", "wallet_5", "pi_real_user")
    email, password, code = _arm_demo(monkeypatch)
    hidden = client.get("/api/connect/astrologers", params={"device": "phone-a"}).json()
    assert hidden["demo_code"] is True
    assert all(item["id"] != "demo-review" for item in hidden["astrologers"])
    assert client.post("/api/connect/demo/unlock", json={"device": "phone-a", "code": "wrong"}).status_code == 401
    assert client.post("/api/connect/sessions", json={
        "device": "phone-b", "astrologer_id": "demo-review", "mode": "chat", "question": "hi",
    }).status_code == 404
    assert client.post("/api/connect/demo/unlock", json={"device": "phone-a", "code": code}).status_code == 200
    shown = client.get("/api/connect/astrologers", params={"device": "phone-a"}).json()["astrologers"]
    demo = next(item for item in shown if item["id"] == "demo-review")
    assert demo["name"] == "Demo Astrologer (App Review)" and demo["demo"] is True and demo["rate_cents"] == 30
    assert all(item["id"] != "demo-review" for item in client.get("/api/connect/astrologers", params={"device": "phone-b"}).json()["astrologers"])
    with db.connect() as con:
        store.ensure(con)
        assert con.execute("select online from ac_astrologer where id='demo-review'").fetchone()[0] == 0
    assert client.post("/api/connect/sessions", json={
        "device": "phone-a", "astrologer_id": "demo-review", "mode": "chat", "question": "hello",
    }).status_code == 402
    added = client.post("/api/connect/demo/credit", json={"device": "phone-a"})
    assert added.status_code == 200 and added.json()["added_cents"] == 500
    real = client.post("/api/connect/sessions", json={
        "device": "phone-a", "astrologer_id": astro_id, "mode": "chat", "question": "real",
    })
    assert real.status_code == 402
    assert client.get("/api/connect/wallet", params={"device": "phone-real"}).json()["balance_cents"] == 500
    base = datetime(2026, 11, 19, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "now", lambda: base)
    started = client.post("/api/connect/sessions", json={
        "device": "phone-a", "astrologer_id": "demo-review", "mode": "chat", "question": "hello",
        "idempotency_key": "demo-chat",
    })
    assert started.status_code == 200
    chat = started.json()
    assert chat["status"] == "active" and chat["demo"] is True and chat["started_at"]
    first = client.get("/api/connect/sessions/" + chat["id"] + "/messages", params={"device": "phone-a"}).json()["messages"]
    assert len(first) == 1 and "App Review" in first[0]["body"] and first[0]["sender"] == "astrologer"
    monkeypatch.setattr(store, "now", lambda: base + timedelta(seconds=60))
    beat = client.post("/api/connect/sessions/" + chat["id"] + "/heartbeat", json={"role": "user", "device": "phone-a"})
    assert beat.status_code == 200 and beat.json()["status"] == "active"
    lines = client.get("/api/connect/sessions/" + chat["id"] + "/messages", params={"device": "phone-a"}).json()["messages"]
    assert len(lines) == 4
    ended = client.post("/api/connect/sessions/" + chat["id"] + "/end", json={"device": "phone-a"})
    assert ended.status_code == 200 and ended.json()["status"] == "ended"
    with db.connect() as con:
        store.ensure(con)
        assert con.execute("select count(*) from ac_earning where astrologer_id='demo-review'").fetchone()[0] == 0
        assert con.execute("select online from ac_astrologer where id='demo-review'").fetchone()[0] == 0
        assert con.execute("select stripe_cents from ac_wallet where device='phone-a'").fetchone()[0] == 0
    assert all(item["astrologer_id"] != "demo-review" for item in store.payout_candidates())
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setenv("LIVEKIT_URL", "wss://live.example")
    monkeypatch.setattr(store, "now", lambda: base)
    call = client.post("/api/connect/sessions", json={
        "device": "phone-a", "astrologer_id": "demo-review", "mode": "call", "question": "call",
        "idempotency_key": "demo-call",
    })
    assert call.status_code == 200 and call.json()["status"] == "active"
    voice = client.post("/api/connect/sessions/" + call.json()["id"] + "/demo-voice", json={"device": "phone-a"})
    assert voice.status_code == 200 and voice.json()["identity"] == "demo-voice"
    claims = connect_livekit.read_jwt(voice.json()["token"], "secret")
    assert claims["sub"] == "demo-voice"
    assert claims["video"]["canPublish"] is True and claims["video"]["canSubscribe"] is False
    assert claims["video"]["room"] == voice.json()["room"]
    assert client.post("/api/connect/sessions/" + call.json()["id"] + "/demo-voice", json={"device": "phone-b"}).status_code == 401
    user_tok = client.post("/api/connect/sessions/" + call.json()["id"] + "/call-token", json={"device": "phone-a"})
    assert user_tok.status_code == 200 and user_tok.json()["identity"].startswith("user-")
    client.post("/api/connect/sessions/" + call.json()["id"] + "/end", json={"device": "phone-a"})
    login = client.post("/api/connect/astro/login", json={"email": email, "password": password})
    assert login.status_code == 200 and login.json()["status"] == "approved" and login.json()["must_change"] is False
    demo_auth = auth(login.json()["token"])
    assert client.post("/api/connect/astro/delete", headers=demo_auth).status_code == 403
    assert client.post("/api/connect/astro/payout", headers=demo_auth, json={"method": "paypal", "paypal_email": "a@b.co", "confirm_email": "a@b.co"}).status_code == 403
    assert client.post("/api/connect/astro/online", headers=demo_auth, json={"online": True}).json()["online"] is True
    with db.connect() as con:
        store.ensure(con)
        assert con.execute("select online, stripe_account from ac_astrologer where id='demo-review'").fetchone() == (0, "")
    assert client.post("/api/connect/block", json={"device": "phone-a", "astrologer_id": "demo-review"}).status_code == 200
    assert all(item["id"] != "demo-review" for item in client.get("/api/connect/astrologers", params={"device": "phone-a"}).json()["astrologers"])
    assert client.post("/api/connect/demo/unlock", json={"device": "phone-a", "code": code}).status_code == 200
    assert any(item["id"] == "demo-review" for item in client.get("/api/connect/astrologers", params={"device": "phone-a"}).json()["astrologers"])
    while True:
        more = client.post("/api/connect/demo/credit", json={"device": "phone-a"}).json()
        if more["added_cents"] == 0:
            assert more["granted_cents"] == 2000
            break
    monkeypatch.setenv("DEMO_REVIEW", "1")
    assert any(item["id"] == "demo-review" for item in client.get("/api/connect/astrologers", params={"device": "phone-b"}).json()["astrologers"])
    monkeypatch.delenv("DEMO_ASTRO_EMAIL", raising=False)
    monkeypatch.delenv("DEMO_ASTRO_PASSWORD", raising=False)
    assert all(item["id"] != "demo-review" for item in client.get("/api/connect/astrologers", params={"device": "phone-b"}).json()["astrologers"])
    root = os.path.join(os.path.dirname(__file__), "..")
    connect = open(os.path.join(root, "web", "connect.js"), encoding="utf-8").read()
    notes = open(os.path.join(root, "ios", "APP_REVIEW_NOTES.md"), encoding="utf-8").read()
    assert "Add review credit" in connect and "App Review code" in connect
    assert "demo-greeting.wav" in connect and "/demo-voice" in connect
    assert "DEMO_ASTRO_EMAIL" in notes and "DEMO_ASTRO_PASSWORD" in notes and "DEMO_REVIEW_CODE" in notes
    assert "3.1.3(d)" in notes and "In-App Purchase" in notes and "listing fee" in notes
    assert password not in notes and password not in connect and code not in notes
    with wave.open(os.path.join(root, "web", "demo-greeting.wav")) as greeting:
        assert greeting.getnchannels() == 1
        assert greeting.getnframes() / greeting.getframerate() > 1


def test_ios_shell_cannot_start_a_listing_plan(client, monkeypatch):
    created = client.post("/api/connect/apply", json={
        "email": "native@example.com", "password": "correct-horse", "name": "Native Rao",
        "specialties": ["career"], "languages": ["English"], "years": 4, "bio": "Timing.", "rate_cents": 100,
    })
    assert created.status_code == 200
    admin = auth("admin-test-token")
    listing = client.get("/api/connect/admin/astrologers", headers=admin).json()["astrologers"]
    astro_id = next(item["id"] for item in listing if item["email"] == "native@example.com")
    assert client.post("/api/connect/admin/astrologers/" + astro_id, headers=admin, json={"action": "approve"}).status_code == 200
    token = client.post("/api/connect/astro/login", json={"email": "native@example.com", "password": "correct-horse"}).json()["token"]
    shell = {**auth(token), "X-Drishti-Shell": "ios"}
    refused = client.post("/api/connect/astro/trial", headers=shell)
    assert refused.status_code == 403 and refused.json()["detail"] == "plan_not_in_app"
    assert "http" not in refused.text
    assert client.post("/api/connect/astro/trial", headers=auth(token)).status_code == 200
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_native")
    monkeypatch.setenv("STRIPE_PRICE_PROVIDER_MONTHLY", "price_month")
    monkeypatch.setenv("APP_PUBLIC_URL", "https://drishti-ios.vercel.app")

    def explode(*_args, **_kwargs):
        raise AssertionError("stripe checkout")

    monkeypatch.setattr(connect_stripe, "create_customer", lambda *a, **k: {"id": "cus_native"})
    monkeypatch.setattr(connect_stripe, "create_checkout", explode)
    monkeypatch.setattr(connect_stripe, "create_portal", explode)
    plan = client.post("/api/connect/astro/plan", headers=shell, json={"interval": "month"})
    portal = client.post("/api/connect/astro/portal", headers=shell)
    assert plan.status_code == 403 and plan.json()["detail"] == "plan_not_in_app"
    assert portal.status_code == 403 and portal.json()["detail"] == "plan_not_in_app"
    assert "url" not in plan.json() and "url" not in portal.json()
    root = os.path.join(os.path.dirname(__file__), "..")
    connect = open(os.path.join(root, "web", "connect.js"), encoding="utf-8").read()
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    notes = open(os.path.join(root, "ios", "APP_REVIEW_NOTES.md"), encoding="utf-8").read()
    assert "function nativeShell()" in connect and "X-Drishti-Shell" in connect
    assert "Your astrologer account isn't active yet." in connect
    assert "id=\"astroRegister\"" in connect
    me = html.split("function pageMe()", 1)[1].split("function peopleHtml", 1)[0]
    assert "inIosApp()" in me and "Sign in" in me and "Register as a live astrologer" in me
    assert "sign-in only" in notes.lower() or "Sign-in only" in notes or "sign-in only" in notes
    assert "listing fee" in notes


def _store_jws(payload):
    import base64
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return "h." + body + ".s"


class _StoreRes:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def _apple_chat(client, txn):
    return client.post("/api/chat", json={
        "device": "phone", "store": "apple", "transaction_id": txn, "signed_transaction": "",
        "profile": {"name": "Asha", "gender": "female", "date": "1990-05-15", "time": "10:30",
                    "place": {"name": "Delhi", "lat": 28.6, "lon": 77.2, "tz": "Asia/Kolkata"}},
        "messages": [{"role": "user", "content": "When?"}],
    })


def test_storekit_errors_reach_chat_as_sse(client, monkeypatch):
    monkeypatch.setattr("llm.configured", lambda: True)

    def invalid(_transaction_id):
        raise connect_storekit.StoreKitError("payment_invalid", "The App Store did not accept that purchase.")

    monkeypatch.setattr(connect_storekit, "fetch_transaction", invalid)
    bad = _apple_chat(client, "txn-invalid")
    assert bad.status_code == 200 and b"payment_invalid" in bad.content
    try:
        store.redeem_apple_question("phone", "txn-invalid", "")
    except store.ConnectError as err:
        assert err.code == "payment_invalid" and err.status == 400
    else:
        raise AssertionError("payment_invalid should be a ConnectError")

    def down(_transaction_id):
        raise connect_storekit.StoreKitError("billing_not_configured", "App Store verification is not set up on the server.")

    monkeypatch.setattr(connect_storekit, "fetch_transaction", down)
    offline = _apple_chat(client, "txn-down")
    assert offline.status_code == 200 and b"billing_not_configured" in offline.content
    assert offline.status_code != 500
    try:
        store.redeem_apple_question("phone", "txn-down", "")
    except store.ConnectError as err:
        assert err.code == "billing_not_configured" and err.status == 503
    else:
        raise AssertionError("billing_not_configured should be a ConnectError")


def test_storekit_404_on_production_retries_sandbox(monkeypatch):
    for name, value in (
        ("APPLE_ISSUER_ID", "iss"),
        ("APPLE_KEY_ID", "kid"),
        ("APPLE_IAP_PRIVATE_KEY", "key"),
        ("APPLE_BUNDLE_ID", "com.ikshana.drishti"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("APPLE_IAP_ENV", raising=False)
    monkeypatch.setattr(connect_storekit, "_bearer", lambda: "token")
    calls = []
    signed = _store_jws({
        "transactionId": "2000001", "productId": "acharya_question", "bundleId": "com.ikshana.drishti",
    })

    def get(url, headers=None, timeout=None):
        calls.append(url)
        if connect_storekit.SANDBOX_HOST in url:
            return _StoreRes(200, {"signedTransactionInfo": signed})
        return _StoreRes(404)

    monkeypatch.setattr(connect_storekit.httpx, "get", get)
    info = connect_storekit.fetch_transaction("2000001")
    assert info["transaction_id"] == "2000001" and info["product_id"] == "acharya_question"
    assert calls[0].startswith(connect_storekit.PRODUCTION_HOST)
    assert calls[1].startswith(connect_storekit.SANDBOX_HOST)

    monkeypatch.setenv("APPLE_IAP_ENV", "production")
    calls.clear()
    with pytest.raises(connect_storekit.StoreKitError) as pinned:
        connect_storekit.fetch_transaction("2000001")
    assert pinned.value.code == "payment_invalid" and len(calls) == 1
    assert calls[0].startswith(connect_storekit.PRODUCTION_HOST)

    monkeypatch.setenv("APPLE_IAP_ENV", "sandbox")
    calls.clear()
    sand = connect_storekit.fetch_transaction("2000001")
    assert sand["product_id"] == "acharya_question" and len(calls) == 1
    assert calls[0].startswith(connect_storekit.SANDBOX_HOST)


def test_ios_store_bridge_maps_storekit_errors():
    root = os.path.join(os.path.dirname(__file__), "..")
    bridge = open(os.path.join(root, "ios", "plugins", "DrishtiNative", "DrishtiBridgeViewController.swift"), encoding="utf-8").read()
    assert "drishtiStore" in bridge and "window.DrishtiStore" in bridge
    assert "__drishtiStoreResolve" in bridge and "__drishtiStoreReject" in bridge
    assert "atDocumentStart" in bridge and "weak var controller" in bridge
    assert "allowsInlineMediaPlayback = true" in bridge
    assert "mediaTypesRequiringUserActionForPlayback = []" in bridge
    html = open(os.path.join(root, "web", "index.html"), encoding="utf-8").read()
    buy = html.split("async function buyPlayQuestion", 1)[1].split("function noteAskError", 1)[0]
    assert "billing_unavailable" in buy and "payment_pending" in buy and "payment_invalid" in buy
    assert "cancelledPlayError()" in buy
    ask = html.split("async function ask", 1)[1].split("const KOOTA", 1)[0]
    fresh = ask.split("const takeToken", 1)[1].split("try { purchaseToken", 1)[0]
    assert "window.DrishtiStore" in fresh and "buyPlayQuestion()" in fresh
    assert fresh.find("buyPlayQuestion()") < fresh.find("buyFreshPlayQuestion")
