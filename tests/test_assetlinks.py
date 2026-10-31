"""Digital Asset Links for the Android Trusted Web Activity."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import app  # noqa: E402

# 32 bytes, written once as lowercase hex and once as a Play Console line.
HEX_A = "ab" * 32
HEX_B = "cd" * 32


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DRISHTI_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ANDROID_SHA256_FINGERPRINTS", raising=False)
    app.clear_play_client()
    from fastapi.testclient import TestClient
    return TestClient(app.app)


def colon(hexes: str) -> str:
    return ":".join(hexes[i:i + 2].upper() for i in range(0, 64, 2))


def test_normalize_accepts_keytool_and_play_console_shapes():
    pretty = colon(HEX_A)
    assert app.normalize_sha256_fingerprint(pretty.lower()) == pretty
    assert app.normalize_sha256_fingerprint("SHA256: " + pretty) == pretty
    assert app.normalize_sha256_fingerprint("sha-256=" + HEX_A) == pretty
    assert app.normalize_sha256_fingerprint("not-a-fingerprint") is None
    assert app.normalize_sha256_fingerprint(HEX_A + "ff") is None


def test_assetlinks_empty_until_fingerprints_are_configured(client):
    got = client.get("/.well-known/assetlinks.json")
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("application/json")
    assert got.json() == []


def test_assetlinks_lists_upload_and_play_signing_keys(client, monkeypatch):
    upload = colon(HEX_A)
    play = "SHA256: " + colon(HEX_B)
    monkeypatch.setenv(
        "ANDROID_SHA256_FINGERPRINTS",
        f"  {upload.lower()} , nope, {play}, {upload}  ",
    )
    got = client.get("/.well-known/assetlinks.json")
    body = got.json()
    assert len(body) == 1
    statement = body[0]
    assert statement["relation"] == ["delegate_permission/common.handle_all_urls"]
    target = statement["target"]
    assert target["namespace"] == "android_app"
    assert target["package_name"] == "com.ikshana.drishti"
    assert target["sha256_cert_fingerprints"] == [upload, colon(HEX_B)]
    json.dumps(body)
