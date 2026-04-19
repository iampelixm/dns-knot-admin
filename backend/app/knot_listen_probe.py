"""Хост для DNS-проб (SOA/DS) из server.listen в knot.conf, если задан явный адрес (не wildcard)."""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any

from app.knot_yaml import parse_knot_conf

logger = logging.getLogger(__name__)

_WILDCARD_ADDRS = frozenset({"0.0.0.0", "::", "*"})


def _listen_strings(server_block: dict[str, Any] | None) -> list[str]:
    if not server_block or not isinstance(server_block, dict):
        return []
    ln = server_block.get("listen")
    if ln is None:
        return []
    if isinstance(ln, str):
        return [ln.strip()] if ln.strip() else []
    if isinstance(ln, list):
        out: list[str] = []
        for item in ln:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                addr = item.get("address")
                port = item.get("port")
                if isinstance(addr, str) and addr.strip():
                    if isinstance(port, int):
                        out.append(f"{addr.strip()}@{port}")
                    elif isinstance(port, str) and port.strip():
                        out.append(f"{addr.strip()}@{port.strip()}")
                    else:
                        out.append(addr.strip())
        return out
    return []


def _host_from_listen_entry(entry: str) -> str | None:
    """Из строки Knot вида «addr@port» вернуть addr (без @port). IPv6: последний @ — перед портом."""
    if "@" not in entry:
        return None
    host, _, _port = entry.rpartition("@")
    host = host.strip()
    if not host:
        return None
    return host


def listen_host_for_dns_probe(knot_conf_text: str) -> str | None:
    """
    Первый не-wildcard адрес из server.listen, пригодный для UDP-запросов из пода dnsadmin.

    Пропускает 0.0.0.0@53, ::@53; для явного IP (или FQDN в listen) возвращает хост.
    Имена интерфейсов (eth0@53) не возвращаем — на стороне dnsadmin они обычно бесполезны.
    """
    try:
        root = parse_knot_conf(knot_conf_text or "")
    except Exception as e:  # noqa: BLE001
        logger.debug("parse knot.conf for listen: %s", e)
        return None
    if not isinstance(root, dict):
        return None
    server = root.get("server")
    if not isinstance(server, dict):
        return None
    for entry in _listen_strings(server):
        host_raw = _host_from_listen_entry(entry)
        if not host_raw:
            continue
        host = host_raw.strip("[]")
        if host in _WILDCARD_ADDRS:
            continue
        try:
            addr = ipaddress.ip_address(host)
            return str(addr)
        except ValueError:
            pass
        # FQDN в listen (редко) — только если похоже на имя хоста, не на интерфейс
        if "." in host and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host):
            return host_raw.strip("[]")
    return None
