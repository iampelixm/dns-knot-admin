"""Проверка ответа Knot по UDP (SOA)."""

from __future__ import annotations

import os
import socket
import time
from typing import Optional, Tuple

import dns.message
import dns.query
import dns.rcode
import dns.rdatatype


def check_authoritative_soa(
    host: str,
    zone: str,
    port: int = 53,
    timeout: float = 2.0,
) -> Tuple[bool, str, Optional[float]]:
    """
    Отправляет UDP SOA-запрос на host:port.
    Возвращает (успех, сообщение, время_мс).
    """
    z = zone.strip().rstrip(".")
    if not z:
        return False, "Пустое имя зоны для проверки", None
    qname = z + "."
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM, proto=socket.IPPROTO_UDP)
    except OSError as e:
        return False, f"Не удалось разрешить {host!r}: {e}", None
    if not infos:
        return False, f"Нет адреса для {host!r}", None
    ip = infos[0][4][0]
    msg = dns.message.make_query(qname, dns.rdatatype.SOA, want_dnssec=False)
    t0 = time.perf_counter()
    try:
        resp = dns.query.udp(msg, ip, port=port, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return False, f"Нет ответа от {host} ({ip}): {e}", None
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    rc = resp.rcode()
    if rc != dns.rcode.NOERROR:
        return False, f"Ответ с кодом {dns.rcode.to_text(rc)}", elapsed_ms
    if not _response_has_soa(resp):
        return False, "В ответе нет SOA", elapsed_ms
    return True, f"SOA для {z} (через {ip})", elapsed_ms


def _response_has_soa(resp: dns.message.Message) -> bool:
    for section in (resp.answer, resp.authority, resp.additional):
        for rrset in section:
            if rrset.rdtype == dns.rdatatype.SOA:
                return True
    return False


def query_soa_serial(
    host: str,
    zone: str,
    port: int = 53,
    timeout: float = 2.0,
) -> Tuple[bool, Optional[int], str]:
    """Запрос SOA serial с конкретного сервера. Возвращает (ok, serial|None, message)."""
    z = zone.strip().rstrip(".") + "."
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM, proto=socket.IPPROTO_UDP)
    except OSError as e:
        return False, None, f"Нет адреса для {host!r}: {e}"
    if not infos:
        return False, None, f"Нет адреса для {host!r}"
    ip = infos[0][4][0]
    msg = dns.message.make_query(z, dns.rdatatype.SOA, want_dnssec=False)
    try:
        resp = dns.query.udp(msg, ip, port=port, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return False, None, f"Нет ответа от {ip}: {e}"
    rc = resp.rcode()
    if rc != dns.rcode.NOERROR:
        return False, None, f"RCODE {dns.rcode.to_text(rc)}"
    for section in (resp.answer, resp.authority):
        for rrset in section:
            if rrset.rdtype == dns.rdatatype.SOA:
                rr = list(rrset)[0]
                return True, int(rr.serial), f"serial={rr.serial}"
    return False, None, "SOA отсутствует в ответе"


def knot_probe(host: str, zone: str | None = None, port: int | None = None, timeout: float = 2.0) -> Tuple[bool, str, Optional[float]]:
    """SOA по UDP на host (имя или IP). Зона и порт из env, если не переданы."""
    z = (zone or os.environ.get("DNS_HEALTH_PROBE_ZONE") or os.environ.get("DEFAULT_ZONE") or "k3s.local").strip()
    p = int(os.environ.get("KNOT_DNS_PORT", "53")) if port is None else port
    return check_authoritative_soa(host, z, port=p, timeout=timeout)


def knot_probe_from_env() -> Tuple[bool, str, Optional[float]]:
    """Устаревший путь: только KNOT_DNS_HOST (часто ClusterIP, не слушает Knot при hostNetwork)."""
    host = os.environ.get("KNOT_DNS_HOST", "knot")
    return knot_probe(host)
