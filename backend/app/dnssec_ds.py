"""Получение DS из опубликованных DNSKEY (без exec в pod Knot)."""

from __future__ import annotations

import socket
from typing import Any, List, Tuple

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


def owner_wire_fqdn(qname: dns.name.Name) -> str:
    """Имя apex с обязательной финальной точкой (как требуют многие регистраторы .RU/.РФ/.SU)."""
    s = qname.to_text()
    return s if s.endswith(".") else f"{s}."


def format_ds_rr_for_registrar(qname: dns.name.Name, ttl: int, ds_rdata: Any) -> str:
    """Полная строка DS: owner TTL IN DS keytag alg digesttype ( hex pairs )."""
    owner = owner_wire_fqdn(qname)
    ds = ds_rdata
    digest_hex = ds.digest.hex().upper()
    pairs = " ".join(digest_hex[i : i + 2] for i in range(0, len(digest_hex), 2))
    return f"{owner} {ttl} IN DS {ds.key_tag} {ds.algorithm} {ds.digest_type} ( {pairs} )"


def format_dnskey_rr_for_registrar(qname: dns.name.Name, ttl: int, rdata: Any) -> str:
    """Полная строка DNSKEY в стиле, близком к примерам ccTLD: owner TTL IN DNSKEY flags proto alg ( multiline base64 )."""
    owner = owner_wire_fqdn(qname)
    txt = rdata.to_text()
    parts = txt.split(None, 3)
    if len(parts) < 4:
        return f"{owner} {ttl} IN DNSKEY {txt}"
    flags, proto, alg, key_b64 = parts[0], parts[1], parts[2], parts[3]
    key_clean = "".join(key_b64.split())
    width = 64
    if len(key_clean) <= width:
        wrapped = f"( {key_clean} )"
    else:
        chunks = [key_clean[i : i + width] for i in range(0, len(key_clean), width)]
        body = "( " + chunks[0]
        for ch in chunks[1:]:
            body += "\n " + ch
        body += " )"
        wrapped = body
    return f"{owner} {ttl} IN DNSKEY {flags} {proto} {alg} {wrapped}"


def zone_is_ru_family(zone: str) -> bool:
    """Домены, для которых ccTLD-регистраторы часто требуют и DNSKEY, и DS (в одном формате)."""
    z = zone.strip().rstrip(".").lower()
    return z.endswith(".ru") or z.endswith(".su") or z.endswith(".рф") or z.endswith(".xn--p1ai")


def fetch_ds_records_for_zone(
    host: str,
    zone: str,
    port: int = 53,
    timeout: float = 3.0,
) -> Tuple[List[str], List[str], str]:
    """
    Запрашивает DNSKEY у авторитативного сервера.

    Возвращает (строки DS SHA-256 для KSK с флагом SEP, строки RR DNSKEY, пояснение).
    Строки в формате полных RR: имя с точкой в конце, DS с дайджестом в скобках, DNSKEY с ключом в скобках.
    """
    z = zone.strip().rstrip(".")
    if not z:
        return [], [], "Пустое имя зоны"
    qname = dns.name.from_text(z)
    msg = dns.message.make_query(qname, dns.rdatatype.DNSKEY, want_dnssec=True)
    ip_udp = _resolve_host_udp_target(host, port)
    resp = dns.query.udp(msg, ip_udp, port=port, timeout=timeout)
    if resp.flags & dns.flags.TC:
        ip_tcp = _resolve_host_tcp_target(host, port)
        resp = dns.query.tcp(msg, ip_tcp, port=port, timeout=timeout)
    if resp.rcode() != dns.rcode.NOERROR:
        return [], [], f"Код ответа {dns.rcode.to_text(resp.rcode())}"
    dnskey_rrset = None
    for rrset in resp.answer:
        if rrset.rdtype == dns.rdatatype.DNSKEY and rrset.name == qname:
            dnskey_rrset = rrset
            break
    if not dnskey_rrset:
        return [], [], "В ответе нет DNSKEY у apex (подпись ещё не опубликована или выключена)"

    ttl = int(dnskey_rrset.ttl)
    dnskey_lines: List[str] = []
    for rdata in dnskey_rrset:
        dnskey_lines.append(format_dnskey_rr_for_registrar(qname, ttl, rdata))

    out_ds: List[str] = []
    for rdata in dnskey_rrset:
        if not (rdata.flags & 1):
            continue
        ds = dns.dnssec.make_ds(qname, rdata, DSDigest.SHA256)
        out_ds.append(format_ds_rr_for_registrar(qname, ttl, ds))
    if not out_ds:
        return [], dnskey_lines, "Нет KSK (SEP) в DNSKEY — нечего выдавать как DS"
    extra = " Формат: полные RR, имя с точкой в конце; DS digest type 2 (SHA-256)."
    if zone_is_ru_family(z):
        extra += " Для .RU/.РФ/.SU передайте регистратору и DNSKEY, и DS."
    return out_ds, dnskey_lines, f"DS SHA-256 для {z} (через {ip_udp}); DNSKEY с сервера.{extra}"
