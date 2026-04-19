"""Получение DS из опубликованных DNSKEY (без exec в pod Knot)."""

from __future__ import annotations

import socket
from typing import List, Tuple

import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rcode
import dns.rdatatype
import dns.dnssec
from dns.dnssectypes import DSDigest


def _resolve_host_udp_target(host: str, port: int) -> str:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM, proto=socket.IPPROTO_UDP)
    if not infos:
        raise OSError(f"Нет адреса для {host!r}")
    return infos[0][4][0]


def _resolve_host_tcp_target(host: str, port: int) -> str:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if not infos:
        raise OSError(f"Нет адреса для {host!r}")
    return infos[0][4][0]


def fetch_ds_records_for_zone(
    host: str,
    zone: str,
    port: int = 53,
    timeout: float = 3.0,
) -> Tuple[List[str], str]:
    """
    Запрашивает DNSKEY у авторитативного сервера, строит DS (SHA-256) для KSK (SEP).

    Возвращает (список строк DS в текстовом виде, пояснение).
    """
    z = zone.strip().rstrip(".")
    if not z:
        return [], "Пустое имя зоны"
    qname = dns.name.from_text(z)
    msg = dns.message.make_query(qname, dns.rdatatype.DNSKEY, want_dnssec=True)
    ip_udp = _resolve_host_udp_target(host, port)
    resp = dns.query.udp(msg, ip_udp, port=port, timeout=timeout)
    if resp.flags & dns.flags.TC:
        ip_tcp = _resolve_host_tcp_target(host, port)
        resp = dns.query.tcp(msg, ip_tcp, port=port, timeout=timeout)
    if resp.rcode() != dns.rcode.NOERROR:
        return [], f"Код ответа {dns.rcode.to_text(resp.rcode())}"
    dnskey_rrset = None
    for rrset in resp.answer:
        if rrset.rdtype == dns.rdatatype.DNSKEY and rrset.name == qname:
            dnskey_rrset = rrset
            break
    if not dnskey_rrset:
        return [], "В ответе нет DNSKEY у apex (подпись ещё не опубликована или выключена)"

    out: List[str] = []
    for rdata in dnskey_rrset:
        if not (rdata.flags & 1):
            continue
        ds = dns.dnssec.make_ds(qname, rdata, DSDigest.SHA256)
        out.append(ds.to_text())
    if not out:
        return [], "Нет KSK (SEP) в DNSKEY — нечего выдавать как DS"
    return out, f"DS SHA-256 для {z} (через {ip_udp})"
