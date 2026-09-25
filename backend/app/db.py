import json
import os
import re
import sqlite3
import hashlib
from typing import Any

from app import crypto_utils

DB_PATH = os.environ.get("DNSADMIN_DB_PATH", "/data/dnsadmin.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS zones (
    zone_name TEXT PRIMARY KEY,
    serial INTEGER NOT NULL DEFAULT 0,
    dnssec_signing INTEGER NOT NULL DEFAULT 0,
    tsig_allowed_keys TEXT NOT NULL DEFAULT '[]',
    deleted INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS zone_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_name TEXT NOT NULL,
    content TEXT NOT NULL,
    serial INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'api',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (zone_name) REFERENCES zones(zone_name)
);

CREATE INDEX IF NOT EXISTS idx_zv_zone_ct ON zone_versions(zone_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_zv_ck ON zone_versions(checksum);

CREATE TABLE IF NOT EXISTS tsig_keys (
    key_id TEXT PRIMARY KEY,
    algorithm TEXT NOT NULL DEFAULT 'hmac-sha256',
    secret_encrypted TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS dns_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    server_type TEXT NOT NULL DEFAULT 'generic',
    address TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 53,
    tsig_key_id TEXT,
    delivery_method TEXT NOT NULL DEFAULT 'manual',
    delivery_config TEXT NOT NULL DEFAULT '{}',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (tsig_key_id) REFERENCES tsig_keys(key_id)
);

CREATE TABLE IF NOT EXISTS zone_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zone_name TEXT NOT NULL,
    server_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'secondary',
    tsig_key_id TEXT,
    serial INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unknown',
    last_ok_at TEXT,
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (zone_name) REFERENCES zones(zone_name) ON DELETE CASCADE,
    FOREIGN KEY (server_id) REFERENCES dns_servers(id) ON DELETE CASCADE,
    UNIQUE(zone_name, server_id)
);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate():
    conn = get_db()
    conn.executescript(_SCHEMA_SQL)
    _check_and_add_column(conn, "zones", "deleted",
                          "ALTER TABLE zones ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    _ensure_table(conn, "dns_servers", """
        CREATE TABLE IF NOT EXISTS dns_servers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            server_type TEXT NOT NULL DEFAULT 'generic',
            address TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 53,
            tsig_key_id TEXT,
            delivery_method TEXT NOT NULL DEFAULT 'manual',
            delivery_config TEXT NOT NULL DEFAULT '{}',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (tsig_key_id) REFERENCES tsig_keys(key_id)
        )
    """)
    _ensure_table(conn, "zone_servers", """
        CREATE TABLE IF NOT EXISTS zone_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone_name TEXT NOT NULL,
            server_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'secondary',
            tsig_key_id TEXT,
            serial INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'unknown',
            last_ok_at TEXT,
            settings TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (zone_name) REFERENCES zones(zone_name) ON DELETE CASCADE,
            FOREIGN KEY (server_id) REFERENCES dns_servers(id) ON DELETE CASCADE,
            UNIQUE(zone_name, server_id)
        )
    """)
    conn.commit()
    conn.close()


def _check_and_add_column(conn: sqlite3.Connection, table: str, column: str, alter_sql: str):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(alter_sql)


def _ensure_table(conn: sqlite3.Connection, table: str, create_sql: str):
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if table not in tables:
        conn.executescript(create_sql)


def setting_get(key: str) -> str | None:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def setting_set(key: str, value: str):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (key, value),
    )
    conn.commit()
    conn.close()


def sha256sum(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def get_latest_version(zone_name: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT id, zone_name, content, serial, checksum, source, created_at "
        "FROM zone_versions WHERE zone_name = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (zone_name,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_version(
    zone_name: str,
    content: str,
    serial: int,
    checksum: str,
    source: str = "api",
) -> int:
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO zones (zone_name, serial) VALUES (?, ?)",
        (zone_name, serial),
    )
    conn.execute(
        "INSERT INTO zone_versions (zone_name, content, serial, checksum, source) "
        "VALUES (?, ?, ?, ?, ?)",
        (zone_name, content, serial, checksum, source),
    )
    vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return vid


_SOA_SERIAL_RE = re.compile(
    r"SOA\s+\S+\s+\S+\s*\(\s*\n\s*(\d+)",
    re.IGNORECASE | re.MULTILINE,
)
_SOA_SERIAL_INLINE_RE = re.compile(
    r"SOA\s+\S+\s+\S+\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+",
    re.IGNORECASE,
)


def _extract_serial(content: str) -> int:
    m = _SOA_SERIAL_RE.search(content)
    if m:
        return int(m.group(1))
    m = _SOA_SERIAL_INLINE_RE.search(content)
    if m:
        return int(m.group(1))
    return 0


def startup_backup(
    zone_files: dict[str, str],
    knot_conf: str | None,
    tsig_keys_data: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    already = setting_get("initial_backup_done")
    if already:
        return {"status": "skipped", "reason": "initial_backup_done already set"}

    zones_imported = 0
    versions_imported = 0
    tsig_keys_imported = 0

    conn = get_db()
    try:
        for zone_name, content in zone_files.items():
            serial = _extract_serial(content)
            checksum = sha256sum(content)
            conn.execute(
                "INSERT OR IGNORE INTO zones (zone_name, serial) VALUES (?, ?)",
                (zone_name, serial),
            )
            conn.execute(
                "INSERT INTO zone_versions (zone_name, content, serial, checksum, source) "
                "VALUES (?, ?, ?, ?, 'initial')",
                (zone_name, content, serial, checksum),
            )
            zones_imported += 1
            versions_imported += 1

        if knot_conf:
            setting_set_raw(conn, "knot_conf", json.dumps(knot_conf))

        if tsig_keys_data:
            for item in tsig_keys_data:
                kid = item["key_id"]
                alg = item.get("algorithm", "hmac-sha256")
                secret_plain = item.get("secret_plaintext", "")
                scope = item.get("scope", "axfr")
                encrypted = crypto_utils.encrypt_secret(secret_plain)
                conn.execute(
                    "INSERT OR IGNORE INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) "
                    "VALUES (?, ?, ?, ?)",
                    (kid, alg, encrypted, scope),
                )
                tsig_keys_imported += 1

        setting_set_raw(conn, "initial_backup_done", "true")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "status": "ok",
        "zones_imported": zones_imported,
        "versions_imported": versions_imported,
        "tsig_keys_imported": tsig_keys_imported,
    }


def setting_set_raw(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (key, value),
    )


def list_zones(include_deleted: bool = False) -> list[dict]:
    conn = get_db()
    query = "SELECT * FROM zones"
    if not include_deleted:
        query += " WHERE deleted = 0"
    query += " ORDER BY zone_name"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_zone(zone_name: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM zones WHERE zone_name = ?", (zone_name,)).fetchone()
    conn.close()
    return dict(row) if row else None


def soft_delete_zone(zone_name: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT deleted FROM zones WHERE zone_name = ?", (zone_name,)
    ).fetchone()
    if not row:
        conn.close()
        return False
    if row["deleted"]:
        conn.close()
        return True  # already deleted
    conn.execute(
        "UPDATE zones SET deleted = 1, updated_at = datetime('now') WHERE zone_name = ?",
        (zone_name,),
    )
    conn.commit()
    conn.close()
    return True


def restore_zone(zone_name: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT deleted FROM zones WHERE zone_name = ?", (zone_name,)
    ).fetchone()
    if not row:
        conn.close()
        return False
    if not row["deleted"]:
        conn.close()
        return True  # already active
    conn.execute(
        "UPDATE zones SET deleted = 0, updated_at = datetime('now') WHERE zone_name = ?",
        (zone_name,),
    )
    conn.commit()
    conn.close()
    return True


def hard_delete_zone(zone_name: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT zone_name FROM zones WHERE zone_name = ?", (zone_name,)).fetchone()
    if not row:
        conn.close()
        return False
    try:
        conn.execute("DELETE FROM zone_versions WHERE zone_name = ?", (zone_name,))
        conn.execute("DELETE FROM zones WHERE zone_name = ?", (zone_name,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return True


def _server_zone_count(server_id: str) -> int:
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM zone_servers WHERE server_id = ?",
        (server_id,),
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def list_dns_servers() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, server_type, address, port, tsig_key_id, "
        "delivery_method, delivery_config, notes, created_at, updated_at "
        "FROM dns_servers ORDER BY name"
    ).fetchall()
    conn.close()
    servers = [dict(r) for r in rows]
    for s in servers:
        s["zone_count"] = _server_zone_count(s["id"])
    return servers


def get_dns_server(server_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT id, name, server_type, address, port, tsig_key_id, "
        "delivery_method, delivery_config, notes, created_at, updated_at "
        "FROM dns_servers WHERE id = ?",
        (server_id,),
    ).fetchone()
    conn.close()
    s = dict(row) if row else None
    if s:
        s["zone_count"] = _server_zone_count(s["id"])
    return s


def create_dns_server(
    server_id: str,
    name: str,
    server_type: str,
    address: str,
    port: int = 53,
    tsig_key_id: str | None = None,
    delivery_method: str = "manual",
    delivery_config: str = "{}",
    notes: str = "",
) -> dict:
    conn = get_db()
    conn.execute(
        "INSERT INTO dns_servers (id, name, server_type, address, port, tsig_key_id, "
        "delivery_method, delivery_config, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (server_id, name, server_type, address, port, tsig_key_id,
         delivery_method, delivery_config, notes),
    )
    conn.commit()
    conn.close()
    return get_dns_server(server_id)


def update_dns_server(
    server_id: str,
    **kwargs,
) -> dict | None:
    allowed = {"name", "server_type", "address", "port", "tsig_key_id",
               "delivery_method", "delivery_config", "notes"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return get_dns_server(server_id)
    sets = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values())
    conn = get_db()
    conn.execute(
        f"UPDATE dns_servers SET {sets}, updated_at = datetime('now') WHERE id = ?",
        (*values, server_id),
    )
    conn.commit()
    row = conn.execute("SELECT changes()").fetchone()
    conn.close()
    if row[0] == 0:
        return None
    return get_dns_server(server_id)


def delete_dns_server(server_id: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT id FROM dns_servers WHERE id = ?", (server_id,)).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute("DELETE FROM dns_servers WHERE id = ?", (server_id,))
    conn.commit()
    conn.close()
    return True


def safe_json_loads(s: str) -> dict:
    """Безопасное чтение JSON; при ошибке возвращает пустой dict."""
    if not s:
        return {}
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return {}


# ── zone_servers (связь зона ↔ сервер) ──


def list_zone_servers(
    zone_name: str | None = None,
    server_id: str | None = None,
) -> list[dict]:
    conn = get_db()
    sql = (
        "SELECT zs.id, zs.zone_name, zs.server_id, zs.role, "
        "zs.tsig_key_id, zs.serial, zs.status, zs.last_ok_at, "
        "zs.settings, zs.created_at, zs.updated_at, "
        "s.name AS server_name, s.server_type, s.address, s.port "
        "FROM zone_servers zs "
        "JOIN dns_servers s ON s.id = zs.server_id "
    )
    params: tuple = ()
    if zone_name:
        sql += " WHERE zs.zone_name = ?"
        params = (zone_name,)
    elif server_id:
        sql += " WHERE zs.server_id = ?"
        params = (server_id,)
    sql += " ORDER BY zs.zone_name, s.name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def assign_zone_to_server(
    zone_name: str,
    server_id: str,
    role: str = "secondary",
    tsig_key_id: str | None = None,
    serial: int = 0,
    settings: str = "{}",
) -> dict | None:
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO zone_servers "
            "(zone_name, server_id, role, tsig_key_id, serial, settings) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (zone_name, server_id, role, tsig_key_id, serial, settings),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return None  # дубликат
    except Exception:
        conn.rollback()
        conn.close()
        return None
    finally:
        conn.close()
    rows = list_zone_servers(zone_name=zone_name)
    for r in rows:
        if r["server_id"] == server_id:
            return r
    return None


def remove_zone_from_server(zone_name: str, server_id: str) -> bool:
    conn = get_db()
    conn.execute(
        "DELETE FROM zone_servers WHERE zone_name = ? AND server_id = ?",
        (zone_name, server_id),
    )
    conn.commit()
    n = conn.execute("SELECT changes()").fetchone()[0]
    conn.close()
    return n > 0


def update_zone_server_status(
    zone_name: str,
    server_id: str,
    serial: int | None = None,
    status: str | None = None,
    last_ok_at: str | None = None,
) -> dict | None:
    updates: dict[str, str | int] = {}
    if serial is not None:
        updates["serial"] = serial
    if status is not None:
        updates["status"] = status
    if last_ok_at is not None:
        updates["last_ok_at"] = last_ok_at
    if not updates:
        return None
    sets = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values())
    conn = get_db()
    conn.execute(
        f"UPDATE zone_servers SET {sets}, updated_at = datetime('now') "
        "WHERE zone_name = ? AND server_id = ?",
        (*values, zone_name, server_id),
    )
    conn.commit()
    conn.close()
    rows = list_zone_servers(zone_name=zone_name)
    for r in rows:
        if r["server_id"] == server_id:
            return r
    return None