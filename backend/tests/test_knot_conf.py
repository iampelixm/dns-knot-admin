"""Тесты парсера knot.conf (dnssec-signing)."""

from __future__ import annotations

import pytest

from app.knot_conf import (
    ensure_symmetric_remotes,
    ensure_zone_in_knot_conf,
    ensure_zone_in_knot_conf_secondary,
    list_zone_dnssec_flags,
    list_zone_field,
    set_zone_dnssec_signing,
    zone_declared_in_knot_conf,
)

SAMPLE = """server:
  listen: 10.0.0.1@53

zone:
  - domain: k3s.local
    file: /zones/k3s.local.zone
    acl: [axfr-allowed]
    dnssec-signing: on

  - domain: summer-site.ru
    file: /zones/summer-site.ru.zone
    acl: [axfr-allowed]
    dnssec-signing: off
"""

NO_SIGNING_LINE = """zone:
  - domain: example.com
    file: /zones/example.com.zone
    acl: [axfr-allowed]
"""


def test_list_flags() -> None:
    f = list_zone_dnssec_flags(SAMPLE)
    assert f["k3s.local"] is True
    assert f["summer-site.ru"] is False


def test_list_default_off_when_line_missing() -> None:
    f = list_zone_dnssec_flags(NO_SIGNING_LINE)
    assert f["example.com"] is False


def test_set_replace_on_to_off() -> None:
    out = set_zone_dnssec_signing(SAMPLE, "k3s.local", False)
    f = list_zone_dnssec_flags(out)
    assert f["k3s.local"] is False
    assert f["summer-site.ru"] is False


def test_set_insert_when_missing() -> None:
    out = set_zone_dnssec_signing(NO_SIGNING_LINE, "example.com", True)
    assert "file: /zones/example.com.zone" in out
    assert "dnssec-signing: on" in out
    assert list_zone_dnssec_flags(out)["example.com"] is True


def test_set_off_to_on_other_unchanged() -> None:
    out = set_zone_dnssec_signing(SAMPLE, "summer-site.ru", True)
    assert list_zone_dnssec_flags(out)["summer-site.ru"] is True
    assert list_zone_dnssec_flags(out)["k3s.local"] is True


def test_zone_declared() -> None:
    assert zone_declared_in_knot_conf(SAMPLE, "k3s.local") is True
    assert zone_declared_in_knot_conf(SAMPLE, "nope.test") is False


def test_unknown_zone_raises() -> None:
    with pytest.raises(ValueError, match="не найдена"):
        set_zone_dnssec_signing(SAMPLE, "missing.zone", True)


def test_trailing_newline_preserved_when_present() -> None:
    sample_nl = SAMPLE + "\n"
    out = set_zone_dnssec_signing(sample_nl, "k3s.local", True)
    assert out.endswith("\n")


def test_dnssec_before_acl_still_found() -> None:
    conf = """zone:
  - domain: z1.test
    dnssec-signing: on
    file: /zones/z1.test.zone
"""
    assert list_zone_dnssec_flags(conf)["z1.test"] is True


def test_zone_block_stops_at_top_level_section() -> None:
    """Блок зоны не должен поглощать последующие top-level секции (remote:/acl:)."""
    conf = """zone:
  - domain: z1.test
    file: /zones/z1.test.zone
    acl: [axfr-allowed]
    dnssec-signing: on
remote:
  - id: peer-master
    address: 1.2.3.4@53
acl:
  - id: peer-master-notify
    action: notify
"""
    assert list_zone_field(conf, "z1.test", "dnssec-signing") == "on"
    assert list_zone_field(conf, "z1.test", "notify") is None
    out = ensure_zone_in_knot_conf(conf, "z1.test", notify=["peer-master"])
    assert list_zone_field(out, "z1.test", "notify") == ["peer-master"]
    assert "remote:\n  - id: peer-master\n" in out
    assert "    notify: [peer-master]" in out
    assert out.count("remote:") == 1


def test_zone_block_top_level_section_kept_untouched() -> None:
    conf = """zone:
  - domain: z1.test
    file: /zones/z1.test.zone
    acl: [axfr-allowed]
    dnssec-signing: on
remote:
  - id: peer-master
    address: 1.2.3.4@53
acl:
  - id: peer-master-notify
    action: notify
"""
    out = ensure_zone_in_knot_conf(conf, "z1.test", notify=["peer-master"])
    assert "  - id: peer-master\n" in out
    assert "  - id: peer-master-notify\n" in out
    assert list_zone_field(out, "z1.test", "notify") == ["peer-master"]


def test_secondary_zone_block_stops_at_top_level_section() -> None:
    conf = """zone:
  - domain: z1.test
    master: [primary-remote]
    acl: [notify-allowed]
    refresh-min-interval: 14400
remote:
  - id: primary-remote
    address: 1.2.3.4@53
"""
    out = ensure_zone_in_knot_conf_secondary(conf, "z1.test", master="peer-first", acl=["peer-first-notify"])
    assert list_zone_field(out, "z1.test", "master") == ["peer-first"]
    assert list_zone_field(out, "z1.test", "refresh-min-interval") == "14400"
    assert "  - id: primary-remote\n" in out


def test_ensure_symmetric_remotes_moves_sections_before_zone() -> None:
    conf = """zone:
  - domain: z1.test
    master: [primary-remote]
    notify: [peer-master]
remote:
  - id: peer-master
    address: 37.230.115.233@53
acl:
  - id: peer-master-notify
    action: notify
key:
  - id: secondary-01
    secret: abc
"""
    out = ensure_symmetric_remotes(conf, [("first", "10.0.0.1")], key_id="secondary-01")
    assert out.index("remote:") < out.index("zone:")
    assert out.index("acl:") < out.index("zone:")
    assert "  - id: peer-first\n" in out
    assert "  - id: peer-first-notify\n" in out
    assert out.index("key:") < out.index("zone:")
