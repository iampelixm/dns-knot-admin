"""Тесты routers/zones.py: мягкое/жёсткое удаление зон через FastAPI TestClient."""

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


def _insert_zone(zone_name: str, serial: int = 100500, deleted: int = 0) -> None:
    conn = _db.get_db()
    conn.execute(
        "INSERT OR IGNORE INTO zones (zone_name, serial, deleted) VALUES (?, ?, ?)",
        (zone_name, serial, deleted),
    )
    conn.commit()
    conn.close()


class TestListZones:
    def test_empty(self, client: TestClient) -> None:
        resp = client.get("/api/zones", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"zones": []}

    def test_returns_active_only(self, client: TestClient) -> None:
        _insert_zone("example.com")
        _insert_zone("deleted.org", deleted=1)

        resp = client.get("/api/zones", headers=_AUTH)
        assert resp.status_code == 200
        zones = resp.json()["zones"]
        assert len(zones) == 1
        assert zones[0]["zone_name"] == "example.com"
        assert zones[0]["deleted"] == 0

    def test_include_deleted(self, client: TestClient) -> None:
        _insert_zone("example.com")
        _insert_zone("deleted.org", deleted=1)

        resp = client.get("/api/zones?include_deleted=true", headers=_AUTH)
        assert resp.status_code == 200
        zones = resp.json()["zones"]
        assert len(zones) == 2
        names = {z["zone_name"] for z in zones}
        assert names == {"example.com", "deleted.org"}


class TestGetZone:
    def test_exists(self, client: TestClient) -> None:
        _insert_zone("example.com", serial=42)

        resp = client.get("/api/zones/example.com", headers=_AUTH)
        assert resp.status_code == 200
        zone = resp.json()["zone"]
        assert zone["zone_name"] == "example.com"
        assert zone["serial"] == 42
        assert "deleted" in zone

    def test_missing(self, client: TestClient) -> None:
        resp = client.get("/api/zones/nonexistent", headers=_AUTH)
        assert resp.status_code == 404


class TestSoftDelete:
    def test_soft_delete_active_zone(self, client: TestClient) -> None:
        _insert_zone("example.com")

        resp = client.delete("/api/zones/example.com", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "soft_deleted"

        conn = _db.get_db()
        row = conn.execute("SELECT deleted FROM zones WHERE zone_name = 'example.com'").fetchone()
        conn.close()
        assert row["deleted"] == 1

    def test_soft_delete_already_deleted(self, client: TestClient) -> None:
        _insert_zone("example.com", deleted=1)

        resp = client.delete("/api/zones/example.com", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "soft_deleted"

    def test_soft_delete_missing(self, client: TestClient) -> None:
        resp = client.delete("/api/zones/nonexistent", headers=_AUTH)
        assert resp.status_code == 404

    def test_hard_delete(self, client: TestClient) -> None:
        _insert_zone("example.com")

        resp = client.delete("/api/zones/example.com?hard=true", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "hard_deleted"

        conn = _db.get_db()
        row = conn.execute("SELECT zone_name FROM zones WHERE zone_name = 'example.com'").fetchone()
        conn.close()
        assert row is None

    def test_hard_deletes_versions(self, client: TestClient) -> None:
        _insert_zone("example.com")
        _db.create_version("example.com", "content-1", 1, "abc")
        _db.create_version("example.com", "content-2", 2, "def")

        resp = client.delete("/api/zones/example.com?hard=true", headers=_AUTH)
        assert resp.status_code == 200

        conn = _db.get_db()
        rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM zone_versions WHERE zone_name = 'example.com'"
        ).fetchone()
        conn.close()
        assert rows["cnt"] == 0


class TestRestore:
    def test_restore_deleted(self, client: TestClient) -> None:
        _insert_zone("example.com", deleted=1)

        resp = client.post("/api/zones/example.com/restore", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "restored"

        conn = _db.get_db()
        row = conn.execute("SELECT deleted FROM zones WHERE zone_name = 'example.com'").fetchone()
        conn.close()
        assert row["deleted"] == 0

    def test_restore_active(self, client: TestClient) -> None:
        _insert_zone("example.com")

        resp = client.post("/api/zones/example.com/restore", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "restored"

    def test_restore_missing(self, client: TestClient) -> None:
        resp = client.post("/api/zones/nonexistent/restore", headers=_AUTH)
        assert resp.status_code == 404


class TestAuth:
    def test_no_token_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/zones")
        assert resp.status_code == 401

    def test_bad_token_returns_401(self, client: TestClient) -> None:
        resp = client.get("/api/zones", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401