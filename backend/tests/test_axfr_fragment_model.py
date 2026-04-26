"""Тесты parse/render фрагмента AXFR (TSIG + ACL)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.axfr_fragment_model import AxfrFragmentModel, AxfrKeyItem, parse_axfr_fragment, render_axfr_fragment

EXAMPLE_YAML = """key:
  - id: secondary-example
    algorithm: hmac-sha256
    secret: CHANGE_ME_BASE64_OR_PLAIN_PER_KEYMGR_OUTPUT

acl:
  - id: axfr-allowed
    action: transfer
    address:
      - 127.0.0.1
      - 198.51.100.0/24
    key: secondary-example
"""


def test_parse_example_shape() -> None:
    model, warn = parse_axfr_fragment(EXAMPLE_YAML)
    assert model is not None
    assert warn is None
    assert len(model.keys) == 1
    assert model.keys[0].id == "secondary-example"
    assert model.keys[0].algorithm == "hmac-sha256"
    assert len(model.acls) == 1
    assert model.acls[0].id == "axfr-allowed"
    assert model.acls[0].action == "transfer"
    assert model.acls[0].address == ["127.0.0.1", "198.51.100.0/24"]
    assert model.acls[0].key == "secondary-example"


def test_round_trip_minimal() -> None:
    yml = """key:
  - id: k1
    algorithm: hmac-sha256
    secret: abcdef
acl:
  - id: a1
    action: transfer
    address:
      - 10.0.0.1
      - 10.0.0.2
    key: k1
"""
    m, w = parse_axfr_fragment(yml)
    assert m is not None and w is None
    out = render_axfr_fragment(m)
    m2, w2 = parse_axfr_fragment(out)
    assert m2 is not None and w2 is None
    assert m2.model_dump() == m.model_dump()


def test_extra_top_level_warning() -> None:
    yml = """other: true
key:
  - id: x
    algorithm: hmac-sha256
    secret: s
acl: []
"""
    m, w = parse_axfr_fragment(yml)
    assert m is not None
    assert w is not None
    assert "other" in w
    assert m.keys[0].id == "x"


def test_empty_fragment() -> None:
    m, w = parse_axfr_fragment("   ")
    assert m is not None
    assert m.keys == [] and m.acls == []
    assert w is None


def test_validate_duplicate_key_id() -> None:
    with pytest.raises(ValidationError):
        AxfrFragmentModel(
            keys=[
                AxfrKeyItem(id="same", secret="a"),
                AxfrKeyItem(id="same", secret="b"),
            ],
            acls=[],
        )
