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
    assert claims["video"]["room"] == issued.json()["room"]
    assert claims["exp"] - claims["nbf"] <= 610
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
    assert captured["data"]["payment_method_types[]"] == "card"
    assert "automatic_payment_methods[enabled]" not in captured["data"]


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


def test_stripe_signature_helper_rejects_a_stale_event():
    payload = b"{}"
    stamp = str(int(time.time()) - 1000)
    sig = hmac.new(b"sec", stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    with pytest.raises(connect_stripe.StripeError):
        connect_stripe.verify_event(payload, f"t={stamp},v1={sig}", "sec")
