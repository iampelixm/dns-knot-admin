"""Формат DS/DNSKEY для регистраторов (.RU и др.)."""

from __future__ import annotations

import dns.name
import dns.rdataclass
import dns.rdatatype
from dns.rdtypes.ANY.DNSKEY import DNSKEY

from app.dnssec_ds import (
    format_dnskey_rr_for_registrar,
    format_ds_rr_for_registrar,
    owner_wire_fqdn,
    zone_is_ru_family,
)
import dns.dnssec
from dns.dnssectypes import DSDigest


def test_owner_trailing_dot() -> None:
    n = dns.name.from_text("test.su")
    assert owner_wire_fqdn(n) == "test.su."


def test_zone_is_ru_family() -> None:
    assert zone_is_ru_family("Example.RU") is True
    assert zone_is_ru_family("x.XN--P1AI") is True
    assert zone_is_ru_family("пример.рф") is True
    assert zone_is_ru_family("t.co") is False


def test_ds_rr_parentheses_and_pairs() -> None:
    z = dns.name.from_text("test.su")
    key = DNSKEY(dns.rdataclass.IN, dns.rdatatype.DNSKEY, flags=257, protocol=3, algorithm=8, key=b"\xaa" * 32)
    ds = dns.dnssec.make_ds(z, key, DSDigest.SHA256)
    line = format_ds_rr_for_registrar(z, 3600, ds)
    assert line.startswith("test.su. 3600 IN DS ")
    assert " ( " in line and " )" in line
    assert " 8 2 " in line  # algorithm 8, digest type 2


def test_dnskey_rr_multiline_parens() -> None:
    z = dns.name.from_text("test.su")
    key_material = b"\x00" * 100
    key = DNSKEY(dns.rdataclass.IN, dns.rdatatype.DNSKEY, flags=257, protocol=3, algorithm=8, key=key_material)
    line = format_dnskey_rr_for_registrar(z, 3600, key)
    assert line.startswith("test.su. 3600 IN DNSKEY 257 3 8 ")
    assert "( " in line
    assert "\n " in line or len(key_material) <= 64
