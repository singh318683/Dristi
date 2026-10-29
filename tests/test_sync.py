"""Birth profiles and Acharya chats survive in the database, and stay there without Postgres."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import db  # noqa: E402
import app  # noqa: E402


ASHA = {
    "id": "p-asha",
    "name": "Asha",
    "relation": "Self",
    "gender": "Female",
    "date": "1990-05-15",
    "time": "10:30",
    "place": {"name": "Delhi, India", "lat": 28.65195, "lon": 77.23149, "tz": "Asia/Kolkata"},
    "offset": None,
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DRISHTI_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from fastapi.testclient import TestClient
    return TestClient(app.app)


def test_postgres_url_is_neon_pooler_safe():
    url = db.normalize_postgres_url("postgres://u:p@ep-abc-pooler.neon.tech/neondb")
    assert url.startswith("postgresql://")
    assert "sslmode=require" in url
    assert db.normalize_postgres_url(url).count("sslmode=") == 1
    assert db.postgres_connect_kwargs()["prepare_threshold"] is None
    assert db.dialect_of("postgresql://h/db") == "postgres"
    assert db.dialect_of("") == "sqlite"


def test_sync_roundtrip_on_sqlite_and_stays_on_one_device(client):
    assert db.dialect_of() == "sqlite"
    saved = client.post("/api/sync", json={
        "device": "phone-1",
        "profiles": [ASHA, {"id": "bad", "name": ""}],
        "active": "p-asha",
        "chats": {"p-asha": [{"role": "user", "content": "How is this year?"}, {"role": "nope", "content": "x"}]},
    })
    assert saved.status_code == 200
    body = saved.json()
    assert body["storage"] == "sqlite"
    assert body["owner"] == "device"
    assert [p["id"] for p in body["profiles"]] == ["p-asha"]
    assert body["active"] == "p-asha"
    assert body["chats"]["p-asha"] == [{"role": "user", "content": "How is this year?"}]

    again = client.get("/api/sync", params={"device": "phone-1"}).json()
    assert again["profiles"][0]["name"] == "Asha"
    assert again["chats"]["p-asha"][0]["content"] == "How is this year?"
    other = client.get("/api/sync", params={"device": "phone-2"}).json()
    assert other["profiles"] == [] and other["chats"] == {}

    cleared = client.post("/api/sync", json={"device": "phone-1", "profiles": [], "chats": {}})
    assert cleared.status_code == 200 and cleared.json()["profiles"] == []


def test_sign_in_moves_this_phones_charts_to_the_account(client):
    client.post("/api/sync", json={"device": "phone-1", "profiles": [ASHA], "active": "p-asha",
                                   "chats": {"p-asha": [{"role": "assistant", "content": "In simple words\nSteady week."}]}})
    client.post("/api/auth/magic", json={"email": "Asha@Example.com", "device": "phone-1"})
    con = db.connect()
    try:
        token = con.execute("select token from magic_links").fetchone()[0]
    finally:
        con.raw.close()
    verified = client.get("/api/auth/verify", params={"token": token, "device": "phone-1"})
    session = verified.json()["session"]
    remote = client.get("/api/sync", params={"device": "phone-2", "session": session}).json()
    assert remote["owner"] == "account" and remote["email"] == "asha@example.com"
    assert remote["profiles"][0]["id"] == "p-asha"
    assert "Steady week" in remote["chats"]["p-asha"][0]["content"]
    left_behind = client.get("/api/sync", params={"device": "phone-1"}).json()
    assert left_behind["profiles"] == []
