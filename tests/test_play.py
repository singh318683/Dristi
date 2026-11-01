"""$0.99 Acharya questions: Play Billing is checked on the server, and nothing else is charged."""
import base64
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import app  # noqa: E402
import db  # noqa: E402
import llm  # noqa: E402
import playbilling  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DELHI = {"name": "Delhi, India", "lat": 28.65195, "lon": 77.23149, "tz": "Asia/Kolkata"}
PROFILE = {"name": "Asha", "gender": "female", "date": "1990-05-15", "time": "10:30", "place": DELHI}
TOKEN = "purchase-token-one-0001"
TOKEN_B = "purchase-token-two-0002"


class FakePlay:
    def __init__(self):
        self.rows = {}
        self.consumed = []
        self.fetches = []

    def add(self, token, **overrides):
        row = {
            "purchaseState": 0,
            "consumptionState": 0,
            "orderId": "GPA." + token[-6:],
            "productId": "acharya_question",
            "quantity": 1,
        }
        row.update(overrides)
        self.rows[token] = row
        return row

    def fetch(self, token):
        self.fetches.append(token)
        if token not in self.rows:
            raise playbilling.PlayError("payment_invalid", "Google Play did not accept this purchase.")
        return dict(self.rows[token])

    def consume(self, token):
        self.consumed.append(token)
        if token in self.rows:
            self.rows[token]["consumptionState"] = 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DRISHTI_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PLAY_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("PLAY_SERVICE_ACCOUNT_FILE", raising=False)
    playbilling.clear_client_cache()
    fake = FakePlay()
    app.set_play_client(fake)
    from fastapi.testclient import TestClient
    try:
        yield TestClient(app.app), fake
    finally:
        app.clear_play_client()


def events(response):
    out = []
    for block in response.text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
    return out


def ask(client, token=TOKEN, device="phone", profile=None):
    body = {
        "device": device,
        "profile": profile or PROFILE,
        "messages": [{"role": "user", "content": "What about my career?"}],
        "lang": "en",
    }
    if token is not None:
        body["purchase_token"] = token
    return client.post("/api/chat", json=body)


def arm(monkeypatch, stream):
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "stream_chat", stream)


async def answer_ok(messages):
    assert any(m["role"] == "user" and "career" in m["content"] for m in messages)
    yield "Career looks steady."


async def answer_dies(messages):
    raise RuntimeError("model down")
    yield "unused"


async def answer_partial(messages):
    yield "Career"
    raise RuntimeError("cut")


def test_validate_rejects_unpaid_promo_and_bundles():
    ok = playbilling.validate_purchase(
        {"purchaseState": 0, "orderId": "GPA.1", "consumptionState": 0, "productId": "acharya_question"},
        "acharya_question")
    assert ok["order_id"] == "GPA.1" and ok["purchase_type"] is None
    tester = playbilling.validate_purchase(
        {"purchaseState": "0", "orderId": "GPA.2", "purchaseType": 0, "quantity": "1"},
        "acharya_question")
    assert tester["purchase_type"] == 0
    with pytest.raises(playbilling.PlayError) as pending:
        playbilling.validate_purchase({"purchaseState": 2, "orderId": "GPA.3"}, "acharya_question")
    assert pending.value.code == "payment_pending"
    with pytest.raises(playbilling.PlayError) as cancelled:
        playbilling.validate_purchase({"purchaseState": 1, "orderId": "GPA.4"}, "acharya_question")
    assert cancelled.value.code == "payment_invalid"
    for purchase_type in (1, 2, 9):
        with pytest.raises(playbilling.PlayError) as promo:
            playbilling.validate_purchase(
                {"purchaseState": 0, "orderId": "GPA.5", "purchaseType": purchase_type}, "acharya_question")
        assert promo.value.code == "payment_invalid"
    with pytest.raises(playbilling.PlayError) as bundle:
        playbilling.validate_purchase(
            {"purchaseState": 0, "orderId": "GPA.6", "quantity": 5}, "acharya_question")
    assert bundle.value.code == "payment_invalid"
    with pytest.raises(playbilling.PlayError):
        playbilling.validate_purchase(
            {"purchaseState": 0, "orderId": "GPA.7", "productId": "credit_pack"}, "acharya_question")
    with pytest.raises(playbilling.PlayError):
        playbilling.validate_purchase({"purchaseState": 0}, "acharya_question")


def test_play_client_talks_to_android_publisher():
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.method == "POST":
            return httpx.Response(204)
        if request.url.path.endswith("/tokens/missing"):
            return httpx.Response(404, json={"error": {"code": 404}})
        if request.url.path.endswith("/tokens/forbidden"):
            return httpx.Response(403, json={"error": {"code": 403}})
        if request.url.path.endswith("/tokens/down"):
            return httpx.Response(503, json={"error": {"code": 503}})
        return httpx.Response(200, json={"purchaseState": 0, "orderId": "GPA.9", "consumptionState": 0})

    client = playbilling.PlayClient(
        "com.ikshana.drishti", "acharya_question",
        {"client_email": "play@example.com", "private_key": "not-used", "token_uri": "https://oauth2.googleapis.com/token"},
        http=httpx.Client(transport=httpx.MockTransport(handler)))
    client._headers = lambda: {"Authorization": "Bearer test-token"}
    body = client.fetch("oktoken")
    assert body["orderId"] == "GPA.9"
    with pytest.raises(playbilling.PlayError) as missing:
        client.fetch("missing")
    assert missing.value.code == "payment_invalid"
    with pytest.raises(playbilling.PlayError) as denied:
        client.fetch("forbidden")
    assert denied.value.code == "billing_not_configured"
    with pytest.raises(playbilling.PlayError) as down:
        client.fetch("down")
    assert down.value.code == "billing_unavailable"
    client.consume("oktoken")
    already = httpx.Response(400, json={"error": {"errors": [{"reason": "productNotOwnedByUser"}]}})
    client._http = httpx.Client(transport=httpx.MockTransport(lambda request: already))
    client.consume("oktoken")  # already consumed is success
    assert seen[0][0] == "GET" and seen[0][2] == "Bearer test-token"
    assert "/applications/com.ikshana.drishti/purchases/products/acharya_question/tokens/oktoken" in seen[0][1]
    assert seen[-1][0] == "POST" and seen[-1][1].endswith(":consume")


def test_service_account_is_required_and_not_invented(tmp_path, monkeypatch):
    monkeypatch.delenv("PLAY_SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.delenv("PLAY_SERVICE_ACCOUNT_FILE", raising=False)
    playbilling.clear_client_cache()
    assert playbilling.client_from_env() is None
    monkeypatch.setenv("PLAY_SERVICE_ACCOUNT_JSON", "{not-json")
    with pytest.raises(playbilling.PlayError) as bad:
        playbilling.client_from_env()
    assert bad.value.code == "billing_not_configured"
    raw = base64.b64encode(json.dumps({
        "client_email": "play@ikshana.example", "private_key": "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----\n",
    }).encode()).decode()
    monkeypatch.setenv("PLAY_SERVICE_ACCOUNT_JSON", raw)
    client = playbilling.client_from_env()
    assert isinstance(client, playbilling.PlayClient)
    assert client.package_name == "com.ikshana.drishti"
    path = tmp_path / "play.json"
    path.write_text(json.dumps({"client_email": "file@ikshana.example", "private_key": "x"}), encoding="utf-8")
    monkeypatch.delenv("PLAY_SERVICE_ACCOUNT_JSON")
    monkeypatch.setenv("PLAY_SERVICE_ACCOUNT_FILE", str(path))
    from_file = playbilling.client_from_env()
    assert from_file._account["client_email"] == "file@ikshana.example"


def test_question_requires_a_verified_purchase(client, monkeypatch):
    http, fake = client
    calls = {"n": 0}

    async def stream(messages):
        calls["n"] += 1
        yield "Career looks steady."

    arm(monkeypatch, stream)
    missing = events(ask(http, token=None))
    assert missing[0]["code"] == "payment_required" and calls["n"] == 0 and fake.fetches == []
    short = events(ask(http, token="too-short"))
    assert short[0]["code"] == "payment_invalid" and calls["n"] == 0 and fake.fetches == []
    fake.add(TOKEN, purchaseState=1, orderId="GPA.CANCEL")
    cancelled = events(ask(http, token=TOKEN))
    assert cancelled[0]["code"] == "payment_invalid" and calls["n"] == 0 and fake.consumed == []
    status = http.get("/api/status").json()
    assert status["free_questions"] == 0 and status["price"] == "0.99"
    assert status["billing"] == "google_play" and status["product_id"] == "acharya_question"
    assert status["play_configured"] is True


def test_paid_question_is_consumed_once_and_cannot_be_reused(client, monkeypatch):
    http, fake = client
    calls = {"n": 0}

    async def stream(messages):
        calls["n"] += 1
        yield "Career looks steady."

    arm(monkeypatch, stream)
    fake.add(TOKEN, purchaseType=0, obfuscatedExternalAccountId="phone")
    fake.add(TOKEN_B, orderId="GPA.SECOND")
    first = events(ask(http, token=TOKEN))
    assert [e["type"] for e in first] == ["token", "done"]
    assert first[1]["charged"] == "play" and first[1]["price"] == "$0.99"
    assert calls["n"] == 1 and fake.consumed == [TOKEN]
    with db.connect() as con:
        row = con.execute(
            "select state, order_id, token, consumed_at from play_purchases").fetchone()
    assert row[0] == "spent" and row[1].startswith("GPA.") and row[2] is None and row[3]
    again = events(ask(http, token=TOKEN))
    assert again[0]["code"] == "payment_used" and calls["n"] == 1
    second = events(ask(http, token=TOKEN_B))
    assert [e["type"] for e in second] == ["token", "done"] and calls["n"] == 2
    assert fake.consumed == [TOKEN, TOKEN_B]


def test_same_order_and_other_device_cannot_double_spend(client, monkeypatch):
    http, fake = client
    arm(monkeypatch, answer_ok)
    fake.add(TOKEN, orderId="GPA.SAME")
    fake.add(TOKEN_B, orderId="GPA.SAME")
    assert events(ask(http, token=TOKEN))[-1]["type"] == "done"
    reused = events(ask(http, token=TOKEN_B, device="phone"))
    assert reused[0]["code"] == "payment_used"
    fake.add("purchase-token-other-009", orderId="GPA.OTHER", obfuscatedExternalAccountId="phone-a")
    stolen = events(ask(http, token="purchase-token-other-009", device="phone-b"))
    assert stolen[0]["code"] == "payment_invalid"


def test_failed_answer_keeps_the_purchase_and_a_partial_answer_spends_it(client, monkeypatch):
    http, fake = client
    fake.add(TOKEN)
    arm(monkeypatch, answer_dies)
    failed = events(ask(http))
    assert failed[0]["code"] == "llm_error" and fake.consumed == []
    with db.connect() as con:
        state = con.execute("select state from play_purchases").fetchone()[0]
    assert state == "released"
    arm(monkeypatch, answer_ok)
    retried = events(ask(http))
    assert retried[-1]["type"] == "done" and fake.consumed == [TOKEN]

    fake.add(TOKEN_B)
    arm(monkeypatch, answer_partial)
    partial = events(ask(http, token=TOKEN_B))
    assert any(e.get("type") == "token" for e in partial)
    assert partial[-1]["code"] == "llm_error"
    assert TOKEN_B in fake.consumed
    blocked = events(ask(http, token=TOKEN_B))
    assert blocked[0]["code"] == "payment_used"


def test_play_not_configured_and_ai_off_do_not_grant_a_question(client, monkeypatch):
    http, fake = client
    calls = {"n": 0}

    async def stream(messages):
        calls["n"] += 1
        yield "nope"

    arm(monkeypatch, stream)
    fake.add(TOKEN)
    app.set_play_client(None)
    refused = events(ask(http))
    assert refused[0]["code"] == "billing_not_configured" and calls["n"] == 0 and fake.consumed == []
    app.set_play_client(fake)
    monkeypatch.setattr(llm, "configured", lambda: False)
    offline = events(ask(http))
    assert offline[0]["code"] == "ai_not_configured" and fake.fetches == [] and fake.consumed == []
    with db.connect() as con:
        assert con.execute("select count(*) from play_purchases").fetchone()[0] == 0


def test_in_progress_purchase_is_not_spent_twice(client, monkeypatch):
    http, fake = client
    fake.add(TOKEN)
    first = app.redeem_purchase("phone", TOKEN)
    with pytest.raises(playbilling.PlayError) as busy:
        app.redeem_purchase("phone", TOKEN)
    assert busy.value.code == "payment_in_progress"
    app.release_purchase(first)
    assert app.redeem_purchase("phone", TOKEN) == first
    with db.connect() as con:
        con.execute("update play_purchases set ts=? where token_hash=?", ("2000-01-01T00:00:00+00:00", first))
    # A reservation older than two minutes can be retried by the same device.
    assert app.redeem_purchase("phone", TOKEN) == first
    with pytest.raises(playbilling.PlayError) as other:
        app.redeem_purchase("other-phone", TOKEN)
    assert other.value.code == "payment_used"
    app.finish_purchase(first)
    with pytest.raises(playbilling.PlayError) as used:
        app.redeem_purchase("phone", TOKEN)
    assert used.value.code == "payment_used"
    assert fake.consumed == [TOKEN]
    arm(monkeypatch, answer_ok)
    closed = events(ask(http, token=None))
    assert closed[0]["code"] == "payment_required"


def test_already_consumed_at_google_is_not_an_answer(client, monkeypatch):
    http, fake = client
    arm(monkeypatch, answer_ok)
    fake.add(TOKEN, consumptionState=1, orderId="GPA.USED")
    got = events(ask(http))
    assert got[0]["code"] == "payment_used"
    with db.connect() as con:
        state = con.execute("select state from play_purchases").fetchone()[0]
    assert state == "spent"


def test_bad_birth_data_releases_the_purchase(client, monkeypatch):
    http, fake = client
    arm(monkeypatch, answer_ok)
    fake.add(TOKEN)
    bad = json.loads(json.dumps(PROFILE))
    bad["place"]["tz"] = "Nope/Nope"
    failed = ask(http, profile=bad)
    assert failed.status_code == 400
    with db.connect() as con:
        assert con.execute("select state from play_purchases").fetchone()[0] == "released"
    assert fake.consumed == []
    ok = events(ask(http))
    assert ok[-1]["type"] == "done" and fake.consumed == [TOKEN]


def test_generic_features_stay_free(client, monkeypatch):
    http, fake = client
    chart = http.post("/api/chart", json=PROFILE)
    assert chart.status_code == 200 and chart.json()["lagna"]["sign"]
    pan = http.get("/api/panchang", params={"lat": 28.65195, "lon": 77.23149, "tz": "Asia/Kolkata", "date": "2026-10-27"})
    assert pan.status_code == 200 and pan.json()["vaar"]["en"] == "Tuesday"
    match = http.post("/api/match", json={"girl": PROFILE, "boy": PROFILE | {"name": "Dev", "gender": "male"}})
    assert match.status_code == 200 and "total" in match.json()
    gita = http.get("/api/gita", params={"date": "2026-10-31"})
    assert gita.status_code == 200 and gita.json()["ref"]
    pdf = http.post("/api/kundli.pdf?lang=en", json=PROFILE)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    async def complete(messages, max_tokens=400):
        return "Cancer Horoscope. The Moon moves through your 1th house."

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    horo = http.get("/api/horoscope", params={"sign": "Cancer", "lang": "en"})
    assert horo.status_code == 200 and "Cancer" in horo.json()["text"]
    assert fake.fetches == [] and fake.consumed == []


def test_ui_states_the_price_and_has_no_packs():
    html = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
    privacy = open(os.path.join(ROOT, "web", "privacy.html"), encoding="utf-8").read()
    assert "$0.99" in html and "purchase_token" in html
    assert "getDigitalGoodsService" in html and "DrishtiPlay" in html
    assert "/api/wallet/topup" not in html
    assert "Credit packs" not in html
    assert "free questions" not in html.lower()
    assert "service.consume" not in html
    assert "$0.99" in privacy and "Google Play" in privacy
    assert "demo only" not in privacy.lower()
    assert "Charged only when the answer starts" in html
    assert "ask-sample" in html and "ask-dots" in html
    assert "Sending buys one $0.99 question" not in html
    assert "priceLabel()" in html
