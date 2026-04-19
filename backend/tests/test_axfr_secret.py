"""Тесты чтения AXFR Secret и генерации TSIG."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from kubernetes.client import V1Secret

from app.axfr_secret import generate_tsig_yaml_fragment, read_axfr_secret
from app.knot_validate import knot_conf_needs_axfr


def test_knot_conf_needs_axfr() -> None:
    assert knot_conf_needs_axfr("include: /etc/knot/conf.d/axfr.conf\n") is True
    assert knot_conf_needs_axfr("include:\n  - /etc/knot/x.conf\n") is True
    assert knot_conf_needs_axfr("include: /other/path\n") is False


def test_read_axfr_ok() -> None:
    import base64

    core = MagicMock()
    core.read_namespaced_secret.return_value = V1Secret(
        data={"axfr.conf": base64.b64encode(b"key:\n  - id: x\n").decode("ascii")}
    )
    st = read_axfr_secret(core, namespace="dns-knot", secret_name="knot-axfr", secret_key="axfr.conf")
    assert st.code == "ok"
    assert st.content == "key:\n  - id: x\n"


def test_read_axfr_not_found() -> None:
    from kubernetes.client.rest import ApiException

    core = MagicMock()
    core.read_namespaced_secret.side_effect = ApiException(status=404)
    st = read_axfr_secret(core, namespace="ns", secret_name="knot-axfr", secret_key="axfr.conf")
    assert st.code == "not_found"
    assert st.content is None
    assert any("kubectl" in h for h in st.hints)


def test_read_axfr_missing_key() -> None:
    import base64

    core = MagicMock()
    core.read_namespaced_secret.return_value = V1Secret(
        data={"other.yaml": base64.b64encode(b"x").decode("ascii")}
    )
    st = read_axfr_secret(core, namespace="ns", secret_name="knot-axfr", secret_key="axfr.conf")
    assert st.code == "missing_key"
    assert "other.yaml" in " ".join(st.hints)


def test_generate_tsig_yaml_fragment_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        assert cmd[0] == "keymgr"
        r = MagicMock()
        r.returncode = 0
        r.stdout = (
            "# hmac-sha256:testid:xxx=\n"
            "key:\n"
            "  - id: testid\n"
            "    algorithm: hmac-sha256\n"
            "    secret: AbCdEfGh=\n"
        )
        r.stderr = ""
        return r

    monkeypatch.setattr("app.axfr_secret.subprocess.run", fake_run)
    yml, kid = generate_tsig_yaml_fragment("testid", with_acl=True, acl_id="axfr-allowed")
    assert kid == "testid"
    assert "secret: AbCdEfGh=" in yml
    assert "acl:" in yml
    assert "key: testid" in yml
