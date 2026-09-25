"""Тесты routers/dns_servers.py: CRUD DNS-серверов через FastAPI TestClient."""

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


class TestListServers:
    def test_empty(self, client: TestClient) -> None:
        resp = client.get("/api/dns-servers", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"servers": []}

    def test_returns_servers(self, client: TestClient) -> None:
        _db.create_dns_server("srv1", "Primary", "knot", "10.0.0.1", 53, notes="main")
        _db.create_dns_server("srv2", "Secondary", "bind", "10.0.0.2", 5353)

        resp = client.get("/api/dns-servers", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["servers"]) == 2
        ids = {s["id"] for s in data["servers"]}
        assert ids == {"srv1", "srv2"}

    def test_types_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/dns-servers/types", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert "types" in data
        assert "knot" in data["types"]
        assert "bind" in data["types"]


class TestCreateServer:
    def test_basic_create(self, client: TestClient) -> None:
        resp = client.post(
            "/api/dns-servers",
            headers=_AUTH,
            json={"id": "srv1", "name": "My Knot", "server_type": "knot", "address": "10.0.0.1"},
        )
        assert resp.status_code == 201
        s = resp.json()["server"]
        assert s["id"] == "srv1"
        assert s["name"] == "My Knot"
        assert s["server_type"] == "knot"
        assert s["address"] == "10.0.0.1"
        assert s["port"] == 53
        assert s["delivery_method"] == "manual"

    def test_create_with_key(self, client: TestClient) -> None:
        resp = client.post(
            "/api/dns-servers",
            headers=_AUTH,
            json={
                "id": "srv1",
                "name": "With Key",
                "server_type": "knot",
                "address": "10.0.0.1",
                "create_key": True,
            },
        )
        assert resp.status_code == 201
        s = resp.json()["server"]
        assert s["tsig_key_id"] is not None
        assert s["tsig_key_id"].startswith("server-")

    def test_create_with_existing_key(self, client: TestClient) -> None:
        conn = _db.get_db()
        encrypted = _db.crypto_utils.encrypt_secret("dGVzdC1zZWNyZXQ=")
        conn.execute(
            "INSERT INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) "
            "VALUES (?, ?, ?, ?)",
            ("my-key", "hmac-sha256", encrypted, "dns_update"),
        )
        conn.commit()
        conn.close()

        resp = client.post(
            "/api/dns-servers",
            headers=_AUTH,
            json={
                "id": "srv1",
                "name": "With Existing Key",
                "server_type": "knot",
                "address": "10.0.0.1",
                "tsig_key_id": "my-key",
            },
        )
        assert resp.status_code == 201
        s = resp.json()["server"]
        assert s["tsig_key_id"] == "my-key"

    def test_create_with_missing_key(self, client: TestClient) -> None:
        resp = client.post(
            "/api/dns-servers",
            headers=_AUTH,
            json={
                "name": "Bad Key",
                "server_type": "knot",
                "address": "10.0.0.1",
                "tsig_key_id": "nonexistent",
            },
        )
        assert resp.status_code == 400

    def test_invalid_type(self, client: TestClient) -> None:
        resp = client.post(
            "/api/dns-servers",
            headers=_AUTH,
            json={"name": "Bad", "server_type": "invalid", "address": "x"},
        )
        assert resp.status_code == 400


class TestGetServer:
    def test_exists(self, client: TestClient) -> None:
        _db.create_dns_server("srv1", "Test", "knot", "10.0.0.1")
        resp = client.get("/api/dns-servers/srv1", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["server"]["id"] == "srv1"

    def test_missing(self, client: TestClient) -> None:
        resp = client.get("/api/dns-servers/nonexistent", headers=_AUTH)
        assert resp.status_code == 404


class TestUpdateServer:
    def test_update_name(self, client: TestClient) -> None:
        _db.create_dns_server("srv1", "Old Name", "knot", "10.0.0.1")
        resp = client.put(
            "/api/dns-servers/srv1",
            headers=_AUTH,
            json={"name": "New Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["server"]["name"] == "New Name"

    def test_update_missing(self, client: TestClient) -> None:
        resp = client.put(
            "/api/dns-servers/nonexistent",
            headers=_AUTH,
            json={"name": "Nope"},
        )
        assert resp.status_code == 404


class TestDeleteServer:
    def test_delete(self, client: TestClient) -> None:
        _db.create_dns_server("srv1", "To Delete", "knot", "10.0.0.1")
        resp = client.delete("/api/dns-servers/srv1", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        conn = _db.get_db()
        row = conn.execute("SELECT id FROM dns_servers WHERE id = 'srv1'").fetchone()
        conn.close()
        assert row is None

    def test_delete_missing(self, client: TestClient) -> None:
        resp = client.delete("/api/dns-servers/nonexistent", headers=_AUTH)
        assert resp.status_code == 404


class TestInstruction:
    def test_instruction_knot(self, client: TestClient) -> None:
        conn = _db.get_db()
        encrypted = _db.crypto_utils.encrypt_secret("c2VjcmV0MTIz")
        conn.execute(
            "INSERT INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) "
            "VALUES (?, ?, ?, ?)",
            ("test-key", "hmac-sha256", encrypted, "dns_update"),
        )
        conn.commit()
        conn.close()
        _db.create_dns_server("srv1", "Test", "knot", "10.0.0.1", tsig_key_id="test-key")

        resp = client.get("/api/dns-servers/srv1/instruction", headers=_AUTH)
        assert resp.status_code == 200
        text = resp.json()["instruction"]
        assert "knot.conf" in text
        assert "test-key" in text
        assert "c2VjcmV0MTIz" in text

    def test_instruction_bind(self, client: TestClient) -> None:
        conn = _db.get_db()
        encrypted = _db.crypto_utils.encrypt_secret("YmluZC1zZWNyZXQ=")
        conn.execute(
            "INSERT INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) "
            "VALUES (?, ?, ?, ?)",
            ("bind-key", "hmac-sha256", encrypted, "axfr"),
        )
        conn.commit()
        conn.close()
        _db.create_dns_server("srv1", "Test BIND", "bind", "10.0.0.1", tsig_key_id="bind-key")

        resp = client.get("/api/dns-servers/srv1/instruction", headers=_AUTH)
        assert resp.status_code == 200
        text = resp.json()["instruction"]
        assert "named.conf" in text
        assert "bind-key" in text

    def test_instruction_no_key(self, client: TestClient) -> None:
        _db.create_dns_server("srv1", "No Key", "generic", "10.0.0.1")
        resp = client.get("/api/dns-servers/srv1/instruction", headers=_AUTH)
        assert resp.status_code == 200
        assert "не назначен TSIG-ключ" in resp.json()["instruction"]

    def test_instruction_missing_server(self, client: TestClient) -> None:
        resp = client.get("/api/dns-servers/nonexistent/instruction", headers=_AUTH)
        assert resp.status_code == 404


class TestAuth:
    def test_no_token_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/dns-servers")
        assert resp.status_code == 401

    def test_bad_token_returns_401(self, client: TestClient) -> None:
        resp = client.get(
            "/api/dns-servers",
            headers={"Authorization": "Bearer bad-token"},
        )
        assert resp.status_code == 401