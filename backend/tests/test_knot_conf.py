"""Тесты парсера knot.conf (dnssec-signing)."""

from __future__ import annotations

import pytest

from app.knot_conf import list_zone_dnssec_flags, set_zone_dnssec_signing, zone_declared_in_knot_conf

SAMPLE = """server:
  listen: 10.0.0.1@53

zone:
  - domain: k3s.local
    file: /zones/k3s.local.zone
    acl: [axfr-allowed]
    dnssec-signing: on

  - domain: summersite.ru
    file: /zones/summersite.ru.zone
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
    assert f["summersite.ru"] is False


def test_list_default_off_when_line_missing() -> None:
    f = list_zone_dnssec_flags(NO_SIGNING_LINE)
    assert f["example.com"] is False


def test_set_replace_on_to_off() -> None:
    out = set_zone_dnssec_signing(SAMPLE, "k3s.local", False)
    f = list_zone_dnssec_flags(out)
    assert f["k3s.local"] is False
    assert f["summersite.ru"] is False


def test_set_insert_when_missing() -> None:
    out = set_zone_dnssec_signing(NO_SIGNING_LINE, "example.com", True)
    assert "file: /zones/example.com.zone" in out
    assert "dnssec-signing: on" in out
    assert list_zone_dnssec_flags(out)["example.com"] is True


def test_set_off_to_on_other_unchanged() -> None:
    out = set_zone_dnssec_signing(SAMPLE, "summersite.ru", True)
    assert list_zone_dnssec_flags(out)["summersite.ru"] is True
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
