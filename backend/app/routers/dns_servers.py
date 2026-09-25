"""CRUD DNS-серверов. Каждый сервер может иметь TSIG-ключ для управления."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app import db as _db

router = APIRouter(prefix="/api/dns-servers", tags=["DNS Servers"])

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)

_VALID_TYPES = {"knot", "bind", "powerdns", "generic"}
_VALID_DELIVERY = {"manual", "agent", "api"}


def _get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Dict[str, Any]:
    if cred is None or cred.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется Bearer-токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not JWT_SECRET:
        raise HTTPException(status_code=500, detail="JWT_SECRET не задан")
    import jwt
    try:
        payload = jwt.decode(cred.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или просроченный токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Неверный токен")
    return payload


_SERVER_TYPES = {
    "knot": {
        "label": "Knot DNS",
        "doc_url": "https://www.knot-dns.cz/docs/",
    },
    "bind": {
        "label": "BIND 9",
        "doc_url": "https://bind9.readthedocs.io/",
    },
    "powerdns": {
        "label": "PowerDNS",
        "doc_url": "https://doc.powerdns.com/",
    },
    "generic": {
        "label": "Generic (RFC 2845)",
        "doc_url": "https://datatracker.ietf.org/doc/html/rfc2845",
    },
}


def _instruction_text(server_type: str, key_id: str, algorithm: str, secret_b64: str) -> str:
    base = (
        f"TSIG-ключ: {key_id}\n"
        f"Algorithm: {algorithm}\n"
        f"Secret (base64): {secret_b64}\n"
    )
    if server_type == "knot":
        return base + (
            "\nДобавьте в knot.conf:\n"
            "```\n"
            f"key:\\n  - id: {key_id}\\n"
            f"    algorithm: {algorithm}\\n"
            f"    secret: {secret_b64}\\n"
            "```\n"
            "После перезапустите Knot: `knotc reload`\n"
        )
    if server_type == "bind":
        return base + (
            "\nДобавьте в named.conf:\n"
            "```\n"
            f'key "{key_id}" {{\n'
            f"    algorithm {algorithm};\n"
            f'    secret "{secret_b64}";\n'
            "}};\n"
            "```\n"
            "После перезапустите BIND: `rndc reload`\n"
        )
    if server_type == "powerdns":
        return base + (
            "\nДобавьте TSIG-ключ через pdnsutil:\n"
            "```\n"
            f"pdnsutil import-tsig-key {key_id} {algorithm} {secret_b64}\n"
            "```\n"
            "Или вручную в БД PowerDNS (таблица tsigkeys).\n"
        )
    return base + (
        "\nНастройте TSIG-аутентификацию согласно документации вашего DNS-сервера.\n"
        f"Key ID: {key_id}\n"
        f"Algorithm: {algorithm}\n"
        f"Secret (base64): {secret_b64}\n"
    )


class CreateServerBody(BaseModel):
    id: str | None = None
    name: str
    server_type: str = "generic"
    address: str
    port: int = Field(default=53, ge=1, le=65535)
    tsig_key_id: str | None = None
    create_key: bool = Field(default=False, description="Создать новый TSIG-ключ для этого сервера")
    delivery_method: str = "manual"
    delivery_config: str = "{}"
    notes: str = ""


class UpdateServerBody(BaseModel):
    name: str | None = None
    server_type: str | None = None
    address: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    tsig_key_id: str | None = None
    delivery_method: str | None = None
    delivery_config: str | None = None
    notes: str | None = None


@router.get("")
def list_servers(
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Список всех DNS-серверов."""
    servers = _db.list_dns_servers()
    return {"servers": servers}


@router.get("/types")
def list_server_types(
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Список поддерживаемых типов DNS-серверов."""
    return {"types": _SERVER_TYPES}


@router.get("/{server_id}")
def get_server(
    server_id: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Получить DNS-сервер по ID."""
    server = _db.get_dns_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Сервер не найден")
    return {"server": server}


@router.get("/{server_id}/instruction")
def get_instruction(
    server_id: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, str]:
    """Сгенерировать инструкцию по настройке TSIG для данного сервера."""
    server = _db.get_dns_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Сервер не найден")
    key_id = server.get("tsig_key_id")
    if not key_id:
        return {"instruction": "Для этого сервера не назначен TSIG-ключ."}
    conn = _db.get_db()
    row = conn.execute(
        "SELECT key_id, algorithm, secret_encrypted FROM tsig_keys WHERE key_id = ?",
        (key_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {"instruction": f"TSIG-ключ '{key_id}' не найден в БД."}
    secret_plain = _db.crypto_utils.decrypt_secret(row["secret_encrypted"])
    text = _instruction_text(
        server["server_type"], row["key_id"], row["algorithm"], secret_plain,
    )
    return {"instruction": text}


def _delivery_commands(server: dict, zones: list[dict]) -> tuple[list[str], list[str]]:
    """Вернуть (setup_commands, post_commands) для сервера."""
    st = server["server_type"]
    znames = [z["zone_name"] for z in zones]
    setup: list[str] = []
    post: list[str] = []

    key_id = server.get("tsig_key_id")
    secret = ""
    alg = ""
    if key_id:
        conn = _db.get_db()
        row = conn.execute(
            "SELECT algorithm, secret_encrypted FROM tsig_keys WHERE key_id = ?",
            (key_id,),
        ).fetchone()
        conn.close()
        if row:
            secret = _db.crypto_utils.decrypt_secret(row["secret_encrypted"])
            alg = row["algorithm"]

    if st == "knot":
        post.append("knotc conf-check")
        post.append("knotc reload")

    elif st == "bind":
        post.append("rndc reconfig")

    elif st == "powerdns":
        if key_id and secret:
            for z in znames:
                setup.append(
                    f"pdnsutil import-tsig-key {key_id} {alg} '{secret}'"
                )
                setup.append(f"pdnsutil create-secondary-zone {z} {server['address']}")
    else:
        # generic — ничего не делаем, только пишем конфиг
        pass

    return setup, post


def _generate_config(server: dict, zones: list[dict]) -> str:
    st = server["server_type"]
    addr = server["address"]
    port = server["port"]
    name = server["name"]
    znames = [z["zone_name"] for z in zones]

    # TSIG key info
    key_id = server.get("tsig_key_id")
    key_block = ""
    if key_id:
        conn = _db.get_db()
        row = conn.execute(
            "SELECT algorithm, secret_encrypted FROM tsig_keys WHERE key_id = ?",
            (key_id,),
        ).fetchone()
        conn.close()
        if row:
            secret = _db.crypto_utils.decrypt_secret(row["secret_encrypted"])
            alg = row["algorithm"]
        else:
            key_id = None
    else:
        alg = ""
        secret = ""

    if st == "knot":
        lines = ["# Knot DNS secondary — generated by dnsadmin", f"# Server: {name}", ""]
        if key_id:
            lines.extend([
                "key:",
                f"  - id: {key_id}",
                f"    algorithm: {alg}",
                f"    secret: {secret}",
                "",
                "remote:",
                f"  - id: dnsadmin",
                f"    address: {addr}@{port}",
                f"    key: {key_id}",
                "",
                "acl:",
                f"  - id: dnsadmin",
                f"    address: {addr}",
                f"    action: notify",
                f"    key: {key_id}",
                "",
            ])
        lines.append("zone:")
        zn = 0
        for z in znames:
            zn += 1
            if key_id:
                lines.append(f"  - domain: {z}")
                lines.append(f"    master: dnsadmin")
                lines.append(f"    acl: dnsadmin")
            else:
                lines.append(f"  - domain: {z}")
                lines.append(f"    master: {addr}@{port}")
        if zn == 0:
            lines.append("  # no zones")
        lines.append("")
        return "\n".join(lines)

    if st == "bind":
        lines = [
            f"// BIND 9 secondary — generated by dnsadmin",
            f"// Server: {name}",
            "",
        ]
        if key_id:
            lines.extend([
                f'key "{key_id}" {{',
                f"    algorithm {alg};",
                f'    secret "{secret}";',
                "};",
                "",
                f'server {addr} {{',
                f"    keys {{ {key_id}; }};",
                "};",
                "",
            ])
        for z in znames:
            if key_id:
                lines.extend([
                    f'zone "{z}" {{',
                    "    type secondary;",
                    f"    primaries {{ {addr} key {key_id}; }};",
                    "};",
                    "",
                ])
            else:
                lines.extend([
                    f'zone "{z}" {{',
                    "    type secondary;",
                    f"    primaries {{ {addr}; }};",
                    "};",
                    "",
                ])
        return "\n".join(lines)

    if st == "powerdns":
        lines = [
            f"# PowerDNS secondary — generated by dnsadmin",
            f"# Server: {name}",
            "",
        ]
        if key_id:
            lines.append(f"# pdnsutil import-tsig-key {key_id} {alg} {secret}")
            lines.append("")
        lines.extend([
            "### Add to pdns.conf:",
            f"master={addr}",
        ])
        if key_id:
            lines.append(f"tsig-key={key_id}")
        lines.append("")
        lines.append("# Add zones as secondary:")
        for z in znames:
            lines.append(f"# pdnsutil create-secondary-zone {z} {addr}")
        lines.append("")
        return "\n".join(lines)

    # generic
    lines = [
        f"# DNS secondary config — generated by dnsadmin",
        f"# Server: {name}",
        f"# Type: {st}",
        f"# Address: {addr}:{port}",
        "",
    ]
    if key_id:
        lines.extend([
            f"# TSIG key: {key_id}",
            f"# Algorithm: {alg}",
            f"# Secret (base64): {secret}",
            "",
        ])
    lines.append(f"# Zones to transfer from {addr}:{port}:")
    for z in znames:
        lines.append(f"#   {z}")
    lines.append("")
    return "\n".join(lines)


@router.get("/{server_id}/config")
def get_config(
    server_id: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, str]:
    """Сгенерировать конфигурацию secondary сервера."""
    server = _db.get_dns_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Сервер не найден")
    zones = _db.list_zones()
    text = _generate_config(server, zones)
    return {"config": text}


@router.post("", status_code=201)
def create_server(
    body: CreateServerBody,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Создать новый DNS-сервер (опционально с авто-генерацией TSIG-ключа)."""
    if body.server_type not in _VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"server_type: {sorted(_VALID_TYPES)}")
    if body.delivery_method not in _VALID_DELIVERY:
        raise HTTPException(status_code=400, detail=f"delivery_method: {sorted(_VALID_DELIVERY)}")

    sid = body.id or uuid.uuid4().hex[:12]

    tsig_key_id = body.tsig_key_id
    if body.create_key:
        conn = _db.get_db()
        key_id = f"server-{sid}"
        raw_secret = os.urandom(32)
        import base64
        secret_b64 = base64.b64encode(raw_secret).decode()
        encrypted = _db.crypto_utils.encrypt_secret(secret_b64)
        conn.execute(
            "INSERT OR IGNORE INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) "
            "VALUES (?, ?, ?, ?)",
            (key_id, "hmac-sha256", encrypted, "dns_update"),
        )
        conn.commit()
        conn.close()
        tsig_key_id = key_id
    elif tsig_key_id:
        conn = _db.get_db()
        row = conn.execute(
            "SELECT key_id FROM tsig_keys WHERE key_id = ?", (tsig_key_id,)
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=400, detail=f"TSIG-ключ '{tsig_key_id}' не найден")

    server = _db.create_dns_server(
        server_id=sid,
        name=body.name,
        server_type=body.server_type,
        address=body.address,
        port=body.port,
        tsig_key_id=tsig_key_id,
        delivery_method=body.delivery_method,
        delivery_config=body.delivery_config,
        notes=body.notes,
    )
    return {"server": server}


@router.put("/{server_id}")
def update_server(
    server_id: str,
    body: UpdateServerBody,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Обновить DNS-сервер (частичное обновление)."""
    server = _db.update_dns_server(
        server_id,
        name=body.name,
        server_type=body.server_type,
        address=body.address,
        port=body.port,
        tsig_key_id=body.tsig_key_id,
        delivery_method=body.delivery_method,
        delivery_config=body.delivery_config,
        notes=body.notes,
    )
    if not server:
        raise HTTPException(status_code=404, detail="Сервер не найден")
    return {"server": server}


@router.delete("/{server_id}")
def delete_server(
    server_id: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, str]:
    """Удалить DNS-сервер."""
    ok = _db.delete_dns_server(server_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Сервер не найден")
    return {"status": "deleted"}


@router.get("/{server_id}/zones")
def list_server_zones(
    server_id: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Список зон, привязанных к серверу."""
    server = _db.get_dns_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Сервер не найден")
    zones = _db.list_zone_servers(server_id=server_id)
    return {"zones": zones}


@router.post("/{server_id}/fetch-config")
def fetch_server_config_from_agent(
    server_id: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Запросить текущий конфиг у сервера (через агента или ConfigMap)."""
    server = _db.get_dns_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Сервер не найден")

    dc = _db.safe_json_loads(server.get("delivery_config", "{}"))
    in_cluster = dc.get("in_cluster", False)

    if in_cluster:
        try:
            from kubernetes import client, config
            cm_name = dc.get("configmap_name") or os.environ.get("KNOT_CONFIGMAP_NAME", "knot-config")
            namespace = os.environ.get("NAMESPACE", "dns-knot")
            config.load_incluster_config()
            core = client.CoreV1Api()
            cm = core.read_namespaced_config_map(cm_name, namespace)
            conf = (cm.data or {}).get("knot.conf", "")
            return {"config": conf}
        except Exception as e:
            return {"error": f"ConfigMap не найден: {e}"}

    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    agent_url = dc.get("agent_url", "").rstrip("/")
    if not agent_url:
        raise HTTPException(status_code=400, detail="delivery_config.agent_url не задан")

    config_path = dc.get("config_path", "/etc/knot/knot.conf")
    api_key = dc.get("api_key", "")

    req = Request(
        f"{agent_url}/config?path={config_path}",
        headers={"X-API-Key": api_key},
        method="GET",
    )
    try:
        with urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Агент ответил {e.code}: {e.read().decode('utf-8')}")
    except URLError as e:
        raise HTTPException(status_code=502, detail=f"Не удалось подключиться к агенту: {e.reason}")


@router.post("/{server_id}/deliver")
def deliver_to_server(
    server_id: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Сгенерировать конфиг и отправить на DNS-сервер."""
    server = _db.get_dns_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Сервер не найден")

    dm = server["delivery_method"]
    if dm not in ("agent", "api"):
        raise HTTPException(status_code=400, detail="Метод доставки не поддерживает доставку")

    dc = _db.safe_json_loads(server.get("delivery_config", "{}"))
    zones = _db.list_zone_servers(server_id=server_id)
    config_text = _generate_config(server, zones)

    in_cluster = dc.get("in_cluster", False)

    if in_cluster:
        from app.main import _apply_knot_conf_and_restart
        cm_name = dc.get("configmap_name") or os.environ.get("KNOT_CONFIGMAP_NAME", "knot-config")
        dep_name = dc.get("deployment_name") or os.environ.get("KNOT_DEPLOYMENT_NAME", "knot")
        try:
            result = _apply_knot_conf_and_restart(config_text, cm_name, dep_name)
        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Ошибка K8s: {e}")

        now = datetime.now(timezone.utc).isoformat()
        for zs in _db.list_zone_servers(server_id=server_id):
            _db.update_zone_server_status(
                zone_name=zs["zone_name"],
                server_id=server_id,
                status="applied",
                last_ok_at=now,
            )
        return {"result": {"ok": True, "message": "ConfigMap обновлён, Knot перезапущен"}}

    # out-of-cluster: send to agent via HTTP
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

    agent_url = dc.get("agent_url", "").rstrip("/")
    if not agent_url:
        raise HTTPException(status_code=400, detail="delivery_config.agent_url не задан")

    config_path = dc.get("config_path", "/etc/knot/knot.conf")
    setup_cmds, post_cmds = _delivery_commands(server, zones)

    reload_cmd = dc.get("reload_cmd", "")
    if reload_cmd:
        post_cmds = [reload_cmd]

    payload = json.dumps({
        "config": config_text,
        "config_path": config_path,
        "setup_commands": setup_cmds,
        "post_commands": post_cmds,
    }).encode("utf-8")

    req = Request(
        f"{agent_url}/apply",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": dc.get("api_key", ""),
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body) if body else {}
    except HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Агент ответил {e.code}: {e.read().decode('utf-8')}")
    except URLError as e:
        raise HTTPException(status_code=502, detail=f"Не удалось подключиться к агенту: {e.reason}")

    if result.get("ok"):
        now = datetime.now(timezone.utc).isoformat()
        for zs in _db.list_zone_servers(server_id=server_id):
            _db.update_zone_server_status(
                zone_name=zs["zone_name"],
                server_id=server_id,
                status="applied",
                last_ok_at=now,
            )

    return {"result": result}