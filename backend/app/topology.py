"""Пер-доменная (per-zone) топология.

Роли серверов задаются в БД (dns_servers + zone_servers).
Fallback на глобальную KNOT_INSTANCES — пока zone_servers не заполнена.

Не импортирует app.main, чтобы не создавать циклическую зависимость.
"""

from __future__ import annotations

import json
import logging
import os

from app import db as _db

logger = logging.getLogger(__name__)

NAMESPACE = os.environ.get("NAMESPACE", "dns-knot")
CONFIGMAP_NAME = os.environ.get("KNOT_CONFIGMAP_NAME", "knot-config")

_ROLE_PRIMARY = "primary"
_ROLE_SECONDARY = "secondary"


def _load_global_instances() -> list[dict]:
    raw = os.environ.get("KNOT_INSTANCES", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        logger.warning("KNOT_INSTANCES: невалидный JSON, глобальная топология пуста")
        return []


def global_instances() -> list[dict]:
    """Кластерные инстансы из KNOT_INSTANCES (глобальная топология, fallback)."""
    return _load_global_instances()


def _primary_global_id() -> str | None:
    for inst in global_instances():
        if inst.get("role") == _ROLE_PRIMARY:
            return inst.get("id")
    return None


def secondary_global_ids() -> list[str]:
    return [
        inst.get("id")
        for inst in global_instances()
        if inst.get("role") == _ROLE_SECONDARY
    ]


def cluster_target(server: dict) -> dict | None:
    """Для in-cluster сервера вернуть (configmap, deployment), иначе None."""
    if server.get("delivery_method") != "api":
        return None
    dc = _db.safe_json_loads(server.get("delivery_config") or "{}")
    if not dc.get("in_cluster"):
        return None
    cm = dc.get("configmap_name") or CONFIGMAP_NAME
    dep = dc.get("deployment_name")
    if not dep:
        return None
    return {"configmap": cm, "deployment": dep}


def in_cluster_servers() -> list[dict]:
    """Все in-cluster серверы (включая назначенные вручную через dns-servers API)."""
    servers = _db.list_dns_servers()
    return [s for s in servers if cluster_target(s)]


def all_in_cluster_targets() -> list[dict]:
    """Все обозримые in-cluster цели: dns_servers + fallback на KNOT_INSTANCES.
    Каждый элемент: {server_id, configmap, deployment}."""
    out: list[dict] = []
    seen: set[str] = set()
    for server in in_cluster_servers():
        t = cluster_target(server)
        if t:
            out.append({"server_id": server["id"], **t})
            seen.add(server["id"])
    for inst in global_instances():
        sid = inst.get("id")
        if not sid or sid in seen:
            continue
        cm = inst.get("configmap") or CONFIGMAP_NAME
        dep = inst.get("deployment")
        if dep:
            out.append({"server_id": sid, "configmap": cm, "deployment": dep})
    return out


def _server_by_id(server_id: str) -> dict | None:
    return _db.get_dns_server(server_id)


def _global_inst_by_id(inst_id: str) -> dict | None:
    for inst in global_instances():
        if inst.get("id") == inst_id:
            return inst
    return None


def get_zone_roles(zone_name: str) -> dict:
    """Роли зоны: {'primary': server_id|None, 'secondaries': [server_id, ...]}. Fallback на глобальную топологию."""
    rows = _db.list_zone_servers(zone_name=zone_name)
    if rows:
        primary = next((r["server_id"] for r in rows if r.get("role") == _ROLE_PRIMARY), None)
        secondaries = [
            r["server_id"] for r in rows
            if r.get("role") == _ROLE_SECONDARY and r["server_id"] != primary
        ]
        return {"primary": primary, "secondaries": secondaries}

    # fallback: глобальная топология из KNOT_INSTANCES
    return {
        "primary": _primary_global_id(),
        "secondaries": secondary_global_ids(),
    }


def zone_primary_target(zone_name: str) -> dict | None:
    """ConfigMap/Deployment primary для зоны (in-cluster) или None."""
    roles = get_zone_roles(zone_name)
    pid = roles.get("primary")
    if not pid:
        return None
    server = _server_by_id(pid)
    if server:
        target = cluster_target(server)
        if target:
            return target
    inst = _global_inst_by_id(pid)
    if inst:
        cm = inst.get("configmap") or CONFIGMAP_NAME
        dep = inst.get("deployment")
        if dep:
            return {"configmap": cm, "deployment": dep}
    return None


def zone_secondary_targets(zone_name: str) -> list[dict]:
    """Список ConfigMap/Deployment secondary (in-cluster) для зоны."""
    roles = get_zone_roles(zone_name)
    out: list[dict] = []
    for sid in roles.get("secondaries", []):
        server = _server_by_id(sid)
        if server:
            target = cluster_target(server)
            if target:
                out.append({**target, "server_id": sid})
                continue
        inst = _global_inst_by_id(sid)
        if inst:
            cm = inst.get("configmap") or CONFIGMAP_NAME
            dep = inst.get("deployment")
            if dep:
                out.append({"configmap": cm, "deployment": dep, "server_id": sid})
    return out


def is_zone_secondary_in_cluster(zone_name: str, server_id: str) -> bool:
    """Сервер server_id — in-cluster secondary для зоны? (учитывая fallback)."""
    target = zone_secondary_targets(zone_name)
    return any(t.get("server_id") == server_id for t in target)


def server_ip(server_id: str) -> str | None:
    """Внешний адрес сервера (для SOA-проб и notify)."""
    server = _server_by_id(server_id)
    if server:
        return server.get("address")
    inst = _global_inst_by_id(server_id)
    return inst.get("ip") if inst else None


def ensure_seeded(core) -> None:
    """Авто-миграция: наполнить dns_servers/zone_servers из глобальной KNOT_INSTANCES.

    - Каждый in-cluster инстанс → запись в dns_servers (delivery_method='api', in_cluster).
    - Для каждой зоны из primary ConfigMap — если zone_servers пуста,
      создаём строки по глобальной топологии (primary/secondary).
    """
    insts = global_instances()
    if not insts:
        return

    created_servers = 0
    for inst in insts:
        sid = inst.get("id")
        if not sid:
            continue
        if _server_by_id(sid):
            continue
        dc = {
            "in_cluster": True,
            "configmap_name": inst.get("configmap") or CONFIGMAP_NAME,
            "deployment_name": inst.get("deployment"),
        }
        try:
            _db.create_dns_server(
                server_id=sid,
                name=inst.get("label") or inst.get("id"),
                server_type="knot",
                address=inst.get("ip") or "",
                port=53,
                delivery_method="api",
                delivery_config=json.dumps(dc, ensure_ascii=False),
                notes="авто-создан из KNOT_INSTANCES",
            )
            created_servers += 1
        except Exception:  # noqa: BLE001
            logger.warning("ensure_seeded: не удалось создать dns_servers=%s", sid, exc_info=True)

    # зоны из primary ConfigMap
    try:
        cm_data = dict((core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE).data) or {})
    except Exception:  # noqa: BLE001
        logger.warning("ensure_seeded: не удалось прочитать primary ConfigMap", exc_info=True)
        return

    zone_names = sorted(k[:-5] for k in cm_data if k.endswith(".zone"))
    seeded_zones = 0
    for zone_name in zone_names:
        if _db.list_zone_servers(zone_name=zone_name):
            continue  # уже есть пер-зона настройка
        roles = get_zone_roles(zone_name)
        pid = roles.get("primary")
        sid_list = [sid for sid in [pid] + roles.get("secondaries", []) if sid]
        if not sid_list:
            continue
        for idx, sid in enumerate(sid_list):
            role = _ROLE_PRIMARY if idx == 0 else _ROLE_SECONDARY
            try:
                _db.assign_zone_to_server(zone_name=zone_name, server_id=sid, role=role)
                seeded_zones += 1
            except Exception:  # noqa: BLE001
                logger.warning("ensure_seeded: не удалось привязать зону=%s сервер=%s", zone_name, sid, exc_info=True)

    logger.info(
        "ensure_seeded: создано dns_servers=%d, связей zone_servers=%d (зон=%d)",
        created_servers,
        seeded_zones,
        len(zone_names),
    )