"""Хост для DNS-проб (SOA/DS) из server.listen в knot.conf, если задан явный адрес (не wildcard)."""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Mapping
from typing import Any

from app.knot_editor_model import extract_editor_model
from app.knot_yaml import parse_knot_conf

logger = logging.getLogger(__name__)

_WILDCARD_ADDRS = frozenset({"0.0.0.0", "::", "*"})

# Если ruamel/структура YAML неожиданна — вытащить явный IPv4@порт с той же строки, что в файле
_RAW_LISTEN_IPV4 = re.compile(
    r"(?im)^\s*listen:\s*[\"']?((?:\d{1,3}\.){3}\d{1,3}@[0-9]+)[\"']?\s*(?:#.*)?$",
)


def _listen_strings(server_block: Any) -> list[str]:
    if server_block is None or not isinstance(server_block, Mapping):
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
            elif isinstance(item, Mapping):
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


def _ip_probe_from_listen_entry(entry: str) -> str | None:
    """Первый пригодный для UDP-проб IP из одной строки listen (addr@port)."""
    host_raw = _host_from_listen_entry(entry.strip())
    if not host_raw:
        return None
    host = host_raw.strip("[]")
    if host in _WILDCARD_ADDRS:
        return None
    try:
        addr = ipaddress.ip_address(host)
        return str(addr)
    except ValueError:
        pass
    if "." in host and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host):
        return host_raw.strip("[]")
    return None


def _fallback_listen_host_from_raw(text: str) -> str | None:
    m = _RAW_LISTEN_IPV4.search(text or "")
    if not m:
        return None
    return _ip_probe_from_listen_entry(m.group(1).strip())


def listen_host_for_dns_probe(knot_conf_text: str) -> str | None:
    """
    Первый не-wildcard адрес из server.listen.

    ruamel отдаёт server как CommentedMap, не dict — поэтому везде Mapping, не isinstance(..., dict).
    """
    text = knot_conf_text or ""
    try:
        root = parse_knot_conf(text)
    except Exception as e:  # noqa: BLE001
        logger.debug("parse knot.conf for listen: %s", e)
        return _fallback_listen_host_from_raw(text)

    try:
        model = extract_editor_model(root)
        lt = (model.server.get("listen") or "").strip()
        for line in lt.splitlines():
            h = _ip_probe_from_listen_entry(line)
            if h:
                return h
    except Exception as e:  # noqa: BLE001
        logger.debug("listen via extract_editor_model: %s", e)

    if isinstance(root, Mapping):
        srv = root.get("server")
        for entry in _listen_strings(srv):
            h = _ip_probe_from_listen_entry(entry)
            if h:
                return h

    return _fallback_listen_host_from_raw(text)
