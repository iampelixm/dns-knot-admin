"""Тесты _apply_zone_update и _knotc_zone_notify."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ZONE_NAME = "example.com"

_ZONE_CONTENT = """\
$ORIGIN example.com.
$TTL 3600
@ IN SOA ns1.example.com. hostmaster.example.com. (
  2024010101 ; serial
  7200       ; refresh
  3600       ; retry
  1209600    ; expire
  300        ; minimum
)
@ IN NS ns1.example.com.
@ IN A 1.2.3.4
"""

_ZONE_CONTENT_CHANGED = """\
$ORIGIN example.com.
$TTL 3600
@ IN SOA ns1.example.com. hostmaster.example.com. (
  2024010101 ; serial
  7200       ; refresh
  3600       ; retry
  1209600    ; expire
  300        ; minimum
)
@ IN NS ns1.example.com.
@ IN A 5.6.7.8
"""


def _make_cm(content: str) -> MagicMock:
    cm = MagicMock()
    cm.data = {f"{ZONE_NAME}.zone": content, "knot.conf": "zone:\n  - domain: example.com\n"}
    return cm


# ---------------------------------------------------------------------------
# _apply_zone_update: no_changes
# ---------------------------------------------------------------------------

class TestApplyZoneUpdateNoChanges:
    def test_returns_no_changes_when_identical(self) -> None:
        from app.main import _apply_zone_update

        core = MagicMock()
        core.read_namespaced_config_map.return_value = _make_cm(_ZONE_CONTENT)
        apps = MagicMock()

        with patch("app.main.get_clients", return_value=(core, apps)):
            result = _apply_zone_update(ZONE_NAME, _ZONE_CONTENT)

        assert result["status"] == "no_changes"
        core.patch_namespaced_config_map.assert_not_called()
        apps.patch_namespaced_deployment.assert_not_called()

    def test_no_changes_ignores_whitespace_differences(self) -> None:
        from app.main import _apply_zone_update

        core = MagicMock()
        core.read_namespaced_config_map.return_value = _make_cm(_ZONE_CONTENT)
        apps = MagicMock()

        with patch("app.main.get_clients", return_value=(core, apps)):
            # добавим пробел в конце — всё равно должно быть no_changes
            result = _apply_zone_update(ZONE_NAME, _ZONE_CONTENT + "\n  \n")

        assert result["status"] == "no_changes"


# ---------------------------------------------------------------------------
# _apply_zone_update: changes → serial bumped, notify sent
# ---------------------------------------------------------------------------

class TestApplyZoneUpdateWithChanges:
    def _run(self, notify_returncode: int = 0) -> dict:
        from app.main import _apply_zone_update

        core = MagicMock()
        core.read_namespaced_config_map.return_value = _make_cm(_ZONE_CONTENT)
        apps = MagicMock()

        fake_proc = SimpleNamespace(returncode=notify_returncode, stderr="")

        with (
            patch("app.main.get_clients", return_value=(core, apps)),
            patch("app.main.subprocess.run", return_value=fake_proc) as mock_run,
        ):
            result = _apply_zone_update(ZONE_NAME, _ZONE_CONTENT_CHANGED)

        return result

    def test_returns_ok(self) -> None:
        result = self._run()
        assert result["status"] == "ok"
        assert "restarted_at" in result

    def test_serial_is_bumped_in_saved_content(self) -> None:
        from app.main import _apply_zone_update

        core = MagicMock()
        core.read_namespaced_config_map.return_value = _make_cm(_ZONE_CONTENT)
        apps = MagicMock()

        fake_proc = SimpleNamespace(returncode=0, stderr="")
        with (
            patch("app.main.get_clients", return_value=(core, apps)),
            patch("app.main.subprocess.run", return_value=fake_proc),
        ):
            _apply_zone_update(ZONE_NAME, _ZONE_CONTENT_CHANGED)

        saved = core.patch_namespaced_config_map.call_args[0][2].data[f"{ZONE_NAME}.zone"]
        # 2024010101 — старая дата в формате YYYYMMDDnn → bump переводит на today01
        assert "2024010101" not in saved
        today_serial = datetime.date.today().strftime("%Y%m%d") + "01"
        assert today_serial in saved

    def test_notify_sent_true_on_success(self) -> None:
        result = self._run(notify_returncode=0)
        assert result["notify_sent"] == "true"

    def test_notify_sent_false_on_knotc_failure(self) -> None:
        result = self._run(notify_returncode=1)
        assert result["notify_sent"] == "false"

    def test_configmap_patched(self) -> None:
        from app.main import _apply_zone_update

        core = MagicMock()
        core.read_namespaced_config_map.return_value = _make_cm(_ZONE_CONTENT)
        apps = MagicMock()

        fake_proc = SimpleNamespace(returncode=0, stderr="")
        with (
            patch("app.main.get_clients", return_value=(core, apps)),
            patch("app.main.subprocess.run", return_value=fake_proc),
        ):
            _apply_zone_update(ZONE_NAME, _ZONE_CONTENT_CHANGED)

        core.patch_namespaced_config_map.assert_called_once()
        apps.patch_namespaced_deployment.assert_called_once()


# ---------------------------------------------------------------------------
# _knotc_zone_notify
# ---------------------------------------------------------------------------

class TestKnotcZoneNotify:
    def test_returns_true_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.main import _knotc_zone_notify

        fake_proc = SimpleNamespace(returncode=0, stderr="")
        monkeypatch.setattr("app.main.subprocess.run", lambda *a, **kw: fake_proc)
        assert _knotc_zone_notify(ZONE_NAME) is True

    def test_returns_false_on_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.main import _knotc_zone_notify

        fake_proc = SimpleNamespace(returncode=1, stderr="error")
        monkeypatch.setattr("app.main.subprocess.run", lambda *a, **kw: fake_proc)
        assert _knotc_zone_notify(ZONE_NAME) is False

    def test_returns_false_when_knotc_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.main import _knotc_zone_notify

        def raise_not_found(*a: object, **kw: object) -> None:
            raise FileNotFoundError

        monkeypatch.setattr("app.main.subprocess.run", raise_not_found)
        assert _knotc_zone_notify(ZONE_NAME) is False
