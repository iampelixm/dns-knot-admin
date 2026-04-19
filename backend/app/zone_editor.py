"""Разбор zone-файла в форму редактора, сборка обратно и проверка синтаксиса (dnspython)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

import dns.name
import dns.rdatatype
import dns.zone
from dns.exception import DNSException


def _origin_name(zone_name: str) -> dns.name.Name:
    z = zone_name.strip().rstrip(".")
    return dns.name.from_text(z)


def validate_zonefile(zone_name: str, text: str) -> Tuple[bool, List[str]]:
    """Проверка: zone-файл парсится как зона с данным origin."""
    if not text or not text.strip():
        return False, ["Пустой zone-файл"]
    try:
        dns.zone.from_text(
            text,
            origin=_origin_name(zone_name),
            relativize=False,
        )
        return True, []
    except DNSException as e:
        return False, [str(e)]
    except Exception as e:  # noqa: BLE001
        return False, [str(e)]


def _email_to_rname(email: str) -> str:
    s = email.strip()
    if not s:
        return "hostmaster."
    if "@" in s:
        user, dom = s.split("@", 1)
        dom = dom.rstrip(".")
        user = user.replace(".", "\\.")
        return f"{user}.{dom}."
    return s if s.endswith(".") else f"{s}."


def _rname_to_email(rname: str) -> str:
    """SOA rname в UI как email (эвристика)."""
    s = rname.strip().rstrip(".")
    if not s:
        return ""
    if "\\" in s:
        return s.replace("\\.", ".")
    parts = s.split(".")
    if len(parts) >= 2 and parts[0]:
        return f"{parts[0]}@{'.'.join(parts[1:])}"
    return s


def zone_text_to_form(zone_name: str, text: str) -> Dict[str, Any]:
    z = dns.zone.from_text(
        text,
        origin=_origin_name(zone_name),
        relativize=False,
    )
    origin = z.origin
    try:
        soa_rr = z.get_rdataset(origin, dns.rdatatype.SOA)
    except KeyError as e:
        raise ValueError("В зоне нет SOA у apex") from e
    if not soa_rr or not soa_rr[0]:
        raise ValueError("В зоне нет SOA у apex")
    ttl_default = int(soa_rr.ttl)
    r0 = soa_rr[0]
    mname = r0.mname.to_text(omit_final_dot=True)
    rname = r0.rname.to_text()
    soa: Dict[str, Any] = {
        "ttl": ttl_default,
        "primary_ns": mname,
        "admin_email": _rname_to_email(rname),
        "serial": int(r0.serial),
        "refresh": int(r0.refresh),
        "retry": int(r0.retry),
        "expire": int(r0.expire),
        "minimum": int(r0.minimum),
    }

    ns_hosts: List[Dict[str, str]] = []
    try:
        ns_rr = z.get_rdataset(origin, dns.rdatatype.NS)
        if ns_rr:
            for rr in ns_rr:
                ns_hosts.append({"host": rr.target.to_text(omit_final_dot=True)})
    except KeyError:
        pass

    records: List[Dict[str, Any]] = []
    for name, rds in z.iterate_rdatasets():
        if rds.rdtype in (dns.rdatatype.SOA, dns.rdatatype.NS) and name == origin:
            continue
        ttl_val = int(rds.ttl)
        rec_ttl = None if ttl_val == ttl_default else ttl_val
        rel = "@" if name == origin else name.relativize(origin).to_text().rstrip(".")
        for rr in rds:
            rtype = dns.rdatatype.to_text(rds.rdtype)
            if rds.rdtype == dns.rdatatype.MX:
                val = f"{rr.preference} {rr.exchange.to_text(omit_final_dot=True)}"
            elif rds.rdtype == dns.rdatatype.TXT:
                val = " ".join(
                    [x.decode() if isinstance(x, bytes) else str(x) for x in rr.strings]
                )
            elif rds.rdtype in (dns.rdatatype.CNAME, dns.rdatatype.DNAME, dns.rdatatype.PTR):
                val = rr.target.to_text(omit_final_dot=True)
            else:
                val = rr.to_text()
            records.append({"name": rel, "ttl": rec_ttl, "rtype": rtype, "value": val})

    return {"soa": soa, "ns": ns_hosts, "records": records}


def _rname_wire(rname: str) -> str:
    s = rname.strip()
    if s.endswith("."):
        return s
    return _email_to_rname(s)


def _format_record_line(owner: str, ttl_part: str, rtype: str, val: str) -> str:
    rt = dns.rdatatype.from_text(rtype)
    ttl_s = ttl_part if ttl_part.strip() else ""
    if rt == dns.rdatatype.MX:
        m = re.match(r"^\s*(\d+)\s+(.+)$", val)
        if not m:
            raise ValueError(f"MX: ожидается «приоритет хост», получено: {val!r}")
        pref = int(m.group(1))
        ex = m.group(2).strip().rstrip(".")
        return f"{owner} {ttl_s}IN MX {pref} {ex}."
    if rt == dns.rdatatype.TXT:
        chunks = [val[i : i + 255] for i in range(0, len(val), 255)]
        quoted = " ".join('"' + c.replace("\\", "\\\\").replace('"', '\\"') + '"' for c in chunks)
        return f"{owner} {ttl_s}IN TXT {quoted}"
    if rt in (dns.rdatatype.CNAME, dns.rdatatype.DNAME, dns.rdatatype.PTR):
        t = val.strip().rstrip(".")
        return f"{owner} {ttl_s}IN {rtype} {t}."
    if rt in (dns.rdatatype.A, dns.rdatatype.AAAA):
        return f"{owner} {ttl_s}IN {rtype} {val.strip()}"
    return f"{owner} {ttl_s}IN {rtype} {val.strip()}"


def form_to_zone_text(zone_name: str, form: Dict[str, Any]) -> str:
    z = zone_name.strip().rstrip(".")
    soa = form.get("soa") or {}
    ttl = int(soa.get("ttl") or 3600)
    primary = str(soa.get("primary_ns") or "").strip().rstrip(".")
    if not primary:
        raise ValueError("Укажите primary NS (mname) в SOA")
    email = str(soa.get("admin_email") or "").strip()
    rname = _email_to_rname(email)
    serial = int(soa.get("serial") or 1)
    refresh = int(soa.get("refresh") or 7200)
    retry = int(soa.get("retry") or 3600)
    expire = int(soa.get("expire") or 1209600)
    minimum = int(soa.get("minimum") or 300)

    lines = [
        f"$ORIGIN {z}.",
        f"$TTL {ttl}",
        f"@ IN SOA {primary}. {_rname_wire(rname)} (",
        f"  {serial} ; serial",
        f"  {refresh} ; refresh",
        f"  {retry} ; retry",
        f"  {expire} ; expire",
        f"  {minimum} ; minimum",
        ")",
    ]

    for ns in form.get("ns") or []:
        h = str(ns.get("host") or "").strip().rstrip(".")
        if h:
            lines.append(f"@ IN NS {h}.")

    for rec in form.get("records") or []:
        name = str(rec.get("name") or "@").strip()
        owner = "@" if name == "@" else name.rstrip(".")
        rtype = str(rec.get("rtype") or "A").strip().upper()
        if rtype in ("SOA", "NS"):
            continue
        val = str(rec.get("value") or "").strip()
        if not val:
            continue
        rec_ttl = rec.get("ttl")
        ttl_part = f"{int(rec_ttl)} " if rec_ttl not in (None, "", 0) else ""
        lines.append(_format_record_line(owner, ttl_part, rtype, val))

    return "\n".join(lines) + "\n"
