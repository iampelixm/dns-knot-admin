"""Тесты извлечения адреса DNS-проб из server.listen."""

from __future__ import annotations

from app.knot_listen_probe import listen_host_for_dns_probe


def test_listen_explicit_ipv4() -> None:
    conf = """
server:
  listen: 37.230.115.233@53
"""
    assert listen_host_for_dns_probe(conf) == "37.230.115.233"


def test_listen_wildcard_skipped_then_ipv4() -> None:
    conf = """
server:
  listen:
    - 0.0.0.0@53
    - 37.230.115.233@53
"""
    assert listen_host_for_dns_probe(conf) == "37.230.115.233"


def test_listen_only_wildcard_returns_none() -> None:
    conf = """
server:
  listen: 0.0.0.0@53
"""
    assert listen_host_for_dns_probe(conf) is None


def test_listen_ipv6() -> None:
    conf = """
server:
  listen: 2001:db8::1@53
"""
    assert listen_host_for_dns_probe(conf) == "2001:db8::1"


def test_no_server_section() -> None:
    assert listen_host_for_dns_probe("zone: []") is None
