"""Astrologer sessions: prices, holds, splits, idempotency, and who may start a call."""
import hashlib
import hmac
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import app  # noqa: E402
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


def test_stripe_signature_helper_rejects_a_stale_event():
    payload = b"{}"
    stamp = str(int(time.time()) - 1000)
    sig = hmac.new(b"sec", stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    with pytest.raises(connect_stripe.StripeError):
        connect_stripe.verify_event(payload, f"t={stamp},v1={sig}", "sec")
