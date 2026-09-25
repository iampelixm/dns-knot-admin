"""Тесты db.py: migrate, CRUD, sha256sum, _extract_serial, startup_backup."""

import json
import os
import tempfile
from base64 import urlsafe_b64encode

import pytest

from app import db

_TEST_ENCRYPTION_KEY = urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()


@pytest.fixture(autouse=True)
def _patch_db_path(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setattr(db, "DB_PATH", tmp)
    db.migrate()
    yield
    os.unlink(tmp)


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", _TEST_ENCRYPTION_KEY)


class TestMigrate:
    def test_tables_created(self) -> None:
        conn = db.get_db()
        tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        conn.close()
        assert "settings" in tables
        assert "zones" in tables
        assert "zone_versions" in tables
        assert "tsig_keys" in tables


class TestSettings:
    def test_get_missing(self) -> None:
        assert db.setting_get("nope") is None

    def test_set_and_get(self) -> None:
        db.setting_set("foo", "bar")
        assert db.setting_get("foo") == "bar"

    def test_replace(self) -> None:
        db.setting_set("x", "1")
        db.setting_set("x", "2")
        assert db.setting_get("x") == "2"


class TestSha256:
    def test_known(self) -> None:
        assert db.sha256sum("hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_empty(self) -> None:
        assert db.sha256sum("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestZoneAndVersions:
    def test_insert_zone(self) -> None:
        conn = db.get_db()
        conn.execute(
            "INSERT INTO zones (zone_name, serial) VALUES (?, ?)",
            ("example.com", 2024010101),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM zones WHERE zone_name = ?", ("example.com",)).fetchone()
        conn.close()
        assert row["serial"] == 2024010101

    def test_insert_version(self) -> None:
        conn = db.get_db()
        conn.execute("INSERT INTO zones (zone_name, serial) VALUES ('test.com', 1)")
        conn.execute(
            "INSERT INTO zone_versions (zone_name, content, serial, checksum, source) "
            "VALUES (?, ?, ?, ?, ?)",
            ("test.com", "content", 1, "abc123", "api"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM zone_versions").fetchone()
        conn.close()
        assert row["checksum"] == "abc123"
        assert row["source"] == "api"

    def test_index_exists(self) -> None:
        conn = db.get_db()
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN ('idx_zv_zone_ct', 'idx_zv_ck')"
        ).fetchall()
        conn.close()
        names = {r["name"] for r in idx}
        assert "idx_zv_zone_ct" in names
        assert "idx_zv_ck" in names


class TestTsigKeys:
    def test_insert_key(self) -> None:
        conn = db.get_db()
        conn.execute(
            "INSERT INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) VALUES (?, ?, ?, ?)",
            ("mykey", "hmac-sha256", "encrypted_secret", "axfr"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tsig_keys WHERE key_id = 'mykey'").fetchone()
        conn.close()
        assert row["scope"] == "axfr"
        assert row["status"] == "active"


ZONE_PAREN_SERIAL = """$ORIGIN example.com.
$TTL 3600
@ IN SOA ns1 admin.example.com (
    2024090101 ; serial
    7200
    3600
    1209600
    300
)
@ IN NS ns1
@ IN A 192.0.2.1
"""

ZONE_INLINE_SERIAL = """$ORIGIN test.com.
$TTL 3600
@ IN SOA ns1 admin.example.com 2024090102 7200 3600 1209600 300
@ IN NS ns1
"""

ZONE_NO_SERIAL = """$ORIGIN bogus.com.
$TTL 3600
@ IN NS ns1
"""


class TestExtractSerial:
    def test_paren_format(self) -> None:
        assert db._extract_serial(ZONE_PAREN_SERIAL) == 2024090101

    def test_inline_format(self) -> None:
        assert db._extract_serial(ZONE_INLINE_SERIAL) == 2024090102

    def test_no_serial(self) -> None:
        assert db._extract_serial(ZONE_NO_SERIAL) == 0

    def test_empty(self) -> None:
        assert db._extract_serial("") == 0


class TestStartupBackup:
    def test_first_run_imports_zones(self) -> None:
        zone_files = {
            "example.com": ZONE_PAREN_SERIAL,
            "test.com": ZONE_INLINE_SERIAL,
        }
        result = db.startup_backup(zone_files, knot_conf="knot: conf", tsig_keys_data=None)

        assert result["status"] == "ok"
        assert result["zones_imported"] == 2
        assert result["versions_imported"] == 2
        assert result["tsig_keys_imported"] == 0

        conn = db.get_db()
        zones = conn.execute("SELECT zone_name, serial FROM zones ORDER BY zone_name").fetchall()
        assert [(r["zone_name"], r["serial"]) for r in zones] == [
            ("example.com", 2024090101),
            ("test.com", 2024090102),
        ]
        versions = conn.execute(
            "SELECT zone_name, serial, source FROM zone_versions ORDER BY zone_name"
        ).fetchall()
        assert [(r["zone_name"], r["serial"], r["source"]) for r in versions] == [
            ("example.com", 2024090101, "initial"),
            ("test.com", 2024090102, "initial"),
        ]
        conn.close()

    def test_first_run_imports_knot_conf(self) -> None:
        db.startup_backup({}, knot_conf="custom knot.conf content")
        stored = db.setting_get("knot_conf")
        assert stored is not None
        assert json.loads(stored) == "custom knot.conf content"

    def test_first_run_imports_tsig_keys(self) -> None:
        tsig_data = [
            {"key_id": "axfr-primary", "algorithm": "hmac-sha256",
             "secret_plaintext": "base64secret1", "scope": "axfr"},
            {"key_id": "axfr-secondary", "algorithm": "hmac-sha256",
             "secret_plaintext": "base64secret2", "scope": "axfr"},
        ]
        result = db.startup_backup({}, knot_conf=None, tsig_keys_data=tsig_data)

        assert result["tsig_keys_imported"] == 2

        conn = db.get_db()
        rows = conn.execute("SELECT key_id, algorithm, scope, status FROM tsig_keys").fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r["status"] == "active"
            assert r["algorithm"] == "hmac-sha256"
        conn.close()

    def test_second_run_skipped(self) -> None:
        db.setting_set("initial_backup_done", "true")
        result = db.startup_backup({"new-zone.com": ZONE_PAREN_SERIAL}, knot_conf=None)
        assert result["status"] == "skipped"

        conn = db.get_db()
        zones = conn.execute("SELECT zone_name FROM zones").fetchall()
        conn.close()
        assert len(zones) == 0  # new-zone.com was NOT imported

    def test_tsig_secret_encrypted(self) -> None:
        tsig_data = [
            {"key_id": "mykey", "algorithm": "hmac-sha256",
             "secret_plaintext": "supersecret", "scope": "dns_update"},
        ]
        db.startup_backup({}, knot_conf=None, tsig_keys_data=tsig_data)

        conn = db.get_db()
        row = conn.execute("SELECT secret_encrypted FROM tsig_keys WHERE key_id = 'mykey'").fetchone()
        conn.close()
        encrypted = row["secret_encrypted"]
        assert encrypted != "supersecret"

        from app.crypto_utils import decrypt_secret
        assert decrypt_secret(encrypted) == "supersecret"


SAMPLE_ZONE = (
    """$ORIGIN test.com.\n$TTL 3600\n@ IN SOA ns1 admin.test.com (\n"""
    """    2024090101\n    7200\n    3600\n    1209600\n    300\n)\n@ IN NS ns1\n"""
)


class TestGetLatestVersion:
    def test_no_versions(self) -> None:
        assert db.get_latest_version("missing.zone") is None

    def test_returns_most_recent(self) -> None:
        conn = db.get_db()
        conn.execute("INSERT INTO zones (zone_name, serial) VALUES ('test.com', 1)")
        conn.execute(
            "INSERT INTO zone_versions (zone_name, content, serial, checksum, source) "
            "VALUES ('test.com', 'v1', 1, 'aaa', 'initial')",
        )
        conn.execute(
            "INSERT INTO zone_versions (zone_name, content, serial, checksum, source) "
            "VALUES ('test.com', 'v2', 2, 'bbb', 'api')",
        )
        conn.commit()
        conn.close()

        latest = db.get_latest_version("test.com")
        assert latest is not None
        assert latest["checksum"] == "bbb"
        assert latest["serial"] == 2
        assert latest["source"] == "api"


class TestCreateVersion:
    def test_creates_and_returns_id(self) -> None:
        conn = db.get_db()
        conn.execute("INSERT INTO zones (zone_name, serial) VALUES ('x.com', 1)")
        conn.commit()
        conn.close()

        vid = db.create_version("x.com", "content123", 100, "chk123", source="import")
        assert isinstance(vid, int)
        assert vid > 0

        row = db.get_latest_version("x.com")
        assert row is not None
        assert row["id"] == vid
        assert row["content"] == "content123"
        assert row["checksum"] == "chk123"
        assert row["source"] == "import"

    def test_multiple_versions(self) -> None:
        conn = db.get_db()
        conn.execute("INSERT INTO zones (zone_name, serial) VALUES ('y.com', 1)")
        conn.commit()
        conn.close()

        v1 = db.create_version("y.com", "v1", 1, "c1", source="initial")
        v2 = db.create_version("y.com", "v2", 2, "c2", source="api")
        assert v2 > v1
        latest = db.get_latest_version("y.com")
        assert latest is not None
        assert latest["id"] == v2