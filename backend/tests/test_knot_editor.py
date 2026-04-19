"""Тесты редактора knot.conf (модель + YAML)."""

from __future__ import annotations

from app.knot_editor_model import KnotEditorModel, apply_editor_model, extract_editor_model
from app.knot_yaml import parse_knot_conf, serialize_knot_conf

SAMPLE = """server:
  listen: 10.0.0.1@53
  identity: ns1.example
  automatic-acl: off

include: /etc/knot/conf.d/axfr.conf

zone:
  - domain: k3s.local
    file: /zones/k3s.local.zone
    acl: [axfr-allowed]
    dnssec-signing: on
"""


def test_extract_roundtrip_model() -> None:
    root = parse_knot_conf(SAMPLE)
    m = extract_editor_model(root)
    assert m.server.get("listen") == "10.0.0.1@53"
    assert m.include == "/etc/knot/conf.d/axfr.conf"
    assert len(m.zone) == 1
    assert m.zone[0].domain == "k3s.local"
    assert m.zone[0].dnssec_signing == "on"
    assert "axfr-allowed" in m.zone[0].acl


def test_apply_preserves_log() -> None:
    src = SAMPLE + "\nlog:\n  - target: stdout\n    any: info\n"
    root = parse_knot_conf(src)
    m = extract_editor_model(root)
    m.server["automatic-acl"] = "on"
    out = apply_editor_model(root, m)
    text = serialize_knot_conf(out)
    assert "automatic-acl: on" in text
    assert "target: stdout" in text


def test_apply_zone_notify_master() -> None:
    root = parse_knot_conf(SAMPLE)
    m = extract_editor_model(root)
    m.zone[0].notify = "10.1.1.1@53\n10.1.1.2@53"
    m.zone[0].master = "primary.remote"
    out = apply_editor_model(root, m)
    z = out["zone"][0]
    assert z["notify"] == ["10.1.1.1@53", "10.1.1.2@53"]
    assert z["master"] == "primary.remote"


def test_put_model_json_shape() -> None:
    """Тело API: server + include + zone."""
    body = {
        "server": {"listen": "0.0.0.0@53", "automatic-acl": "off"},
        "include": "/etc/knot/conf.d/axfr.conf",
        "zone": [
            {
                "domain": "z.test",
                "file": "/zones/z.test.zone",
                "acl": "axfr-allowed",
                "dnssec-signing": "off",
            }
        ],
    }
    model = KnotEditorModel.model_validate(body)
    root = parse_knot_conf("server: {}\nzone: []\n")
    # пустой zone список в исходнике — заменим
    root["zone"] = []
    out = apply_editor_model(root, model)
    assert out["zone"][0]["domain"] == "z.test"
