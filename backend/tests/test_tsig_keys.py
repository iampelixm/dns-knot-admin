"""Тесты routers/tsig_keys.py: CRUD TSIG-ключей через FastAPI TestClient."""

import json
import os
import tempfile
from base64 import urlsafe_b64encode

import jwt
import pytest
from fastapi.testclient import TestClient

from app import db as _db

JWT_SECRET = "test-jwt-secret-for-testing"
TOKEN = jwt.encode({"sub": "admin"}, JWT_SECRET, algorithm="HS256")

_ENCRYPTION_KEY = urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setattr(_db, "DB_PATH", tmp)
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", _ENCRYPTION_KEY)
    _db.migrate()
    yield
    os.unlink(tmp)


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


_AUTH = {"Authorization": f"Bearer {TOKEN}"}


class TestListKeys:
    def test_empty(self, client: TestClient) -> None:
        resp = client.get("/api/tsig-keys", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"keys": []}

    def test_returns_keys(self, client: TestClient) -> None:
        conn = _db.get_db()
        conn.execute(
            "INSERT INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) VALUES (?, ?, ?, ?)",
            ("k1", "hmac-sha256", "enc1", "axfr"),
        )
        conn.execute(
            "INSERT INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) VALUES (?, ?, ?, ?)",
            ("k2", "hmac-sha256", "enc2", "dns_update"),
        )
        conn.commit()
        conn.close()

        resp = client.get("/api/tsig-keys", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["keys"]) == 2
        key_ids = {k["key_id"] for k in data["keys"]}
        assert key_ids == {"k1", "k2"}
        # secret_encrypted не возвращается
        for k in data["keys"]:
            assert "secret_encrypted" not in k


class TestCreateKey:
    def test_creates_and_returns_secret(self, client: TestClient) -> None:
        resp = client.post(
            "/api/tsig-keys",
            headers=_AUTH,
            json={"name": "my-key", "scope": "dns_update"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["key_id"] == "my-key"
        assert body["scope"] == "dns_update"
        assert body["status"] == "active"
        assert len(body["secret"]) > 0  # base64 secret returned once

        # Verify it's stored encrypted
        conn = _db.get_db()
        row = conn.execute("SELECT * FROM tsig_keys WHERE key_id = 'my-key'").fetchone()
        conn.close()
        assert row is not None
        assert row["secret_encrypted"] != body["secret"]
        assert row["scope"] == "dns_update"

    def test_missing_name(self, client: TestClient) -> None:
        resp = client.post(
            "/api/tsig-keys",
            headers=_AUTH,
            json={"scope": "axfr"},
        )
        assert resp.status_code == 400

    def test_invalid_scope(self, client: TestClient) -> None:
        resp = client.post(
            "/api/tsig-keys",
            headers=_AUTH,
            json={"name": "bad-scope-key", "scope": "invalid"},
        )
        assert resp.status_code == 400

    def test_duplicate_key_id(self, client: TestClient) -> None:
        client.post("/api/tsig-keys", headers=_AUTH, json={"name": "dup", "scope": "axfr"})
        resp = client.post("/api/tsig-keys", headers=_AUTH, json={"name": "dup", "scope": "axfr"})
        assert resp.status_code == 409


class TestRevokeKey:
    def test_revoke_existing(self, client: TestClient) -> None:
        conn = _db.get_db()
        conn.execute(
            "INSERT INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) VALUES (?, ?, ?, ?)",
            ("mykey", "hmac-sha256", "enc", "axfr"),
        )
        conn.commit()
        conn.close()

        resp = client.post("/api/tsig-keys/mykey/revoke", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

        conn = _db.get_db()
        row = conn.execute("SELECT status FROM tsig_keys WHERE key_id = 'mykey'").fetchone()
        conn.close()
        assert row["status"] == "revoked"

    def test_revoke_already_revoked(self, client: TestClient) -> None:
        conn = _db.get_db()
        conn.execute(
            "INSERT INTO tsig_keys (key_id, algorithm, secret_encrypted, scope, status) "
            "VALUES (?, ?, ?, ?, 'revoked')",
            ("mykey", "hmac-sha256", "enc", "axfr"),
        )
        conn.commit()
        conn.close()

        resp = client.post("/api/tsig-keys/mykey/revoke", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_revoked"

    def test_revoke_missing(self, client: TestClient) -> None:
        resp = client.post("/api/tsig-keys/nonexistent/revoke", headers=_AUTH)
        assert resp.status_code == 404


class TestDeleteKey:
    def test_delete_existing(self, client: TestClient) -> None:
        conn = _db.get_db()
        conn.execute(
            "INSERT INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) VALUES (?, ?, ?, ?)",
            ("mykey", "hmac-sha256", "enc", "axfr"),
        )
        conn.commit()
        conn.close()

        resp = client.delete("/api/tsig-keys/mykey", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        conn = _db.get_db()
        row = conn.execute("SELECT key_id FROM tsig_keys WHERE key_id = 'mykey'").fetchone()
        conn.close()
        assert row is None

    def test_delete_missing(self, client: TestClient) -> None:
        resp = client.delete("/api/tsig-keys/nonexistent", headers=_AUTH)
        assert resp.status_code == 404


class TestAuth:
    def test_no_token_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/tsig-keys")
        assert resp.status_code == 401

    def test_bad_token_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/tsig-keys", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401