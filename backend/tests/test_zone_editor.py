"""Тесты zone_editor: bump_soa_serial, apply_serial_bump."""

from __future__ import annotations

import datetime

import pytest

from app.zone_editor import apply_serial_bump, bump_soa_serial

# ---------------------------------------------------------------------------
# bump_soa_serial
# ---------------------------------------------------------------------------

ZONE_NAME = "example.com"

_MULTILINE_ZONE = """\
$ORIGIN example.com.
$TTL 3600
@ IN SOA ns1.example.com. hostmaster.example.com. (
  {serial} ; serial
  7200      ; refresh
  3600      ; retry
  1209600   ; expire
  300       ; minimum
)
@ IN NS ns1.example.com.
@ IN A 1.2.3.4
"""

_INLINE_ZONE = (
    "$ORIGIN example.com.\n"
    "$TTL 3600\n"
    "@ IN SOA ns1.example.com. hostmaster.example.com."
    " {serial} 7200 3600 1209600 300\n"
    "@ IN NS ns1.example.com.\n"
    "@ IN A 1.2.3.4\n"
)


def _zone(serial: int, *, inline: bool = False) -> str:
    tpl = _INLINE_ZONE if inline else _MULTILINE_ZONE
    return tpl.format(serial=serial)


def _today_prefix() -> str:
    return datetime.date.today().strftime("%Y%m%d")


class TestBumpSoaSerial:
    def test_date_format_today_increments_nn(self) -> None:
        serial = int(_today_prefix() + "05")
        result = bump_soa_serial(serial)
        assert result == int(_today_prefix() + "06")

    def test_date_format_today_nn_at_max(self) -> None:
        serial = int(_today_prefix() + "99")
        result = bump_soa_serial(serial)
        # остаётся в пределах 99 — не уходит за формат
        assert result == int(_today_prefix() + "99")

    def test_date_format_old_date_resets_to_today(self) -> None:
        serial = 2020010101
        result = bump_soa_serial(serial)
        assert result == int(_today_prefix() + "01")

    def test_arbitrary_serial_increments_by_one(self) -> None:
        assert bump_soa_serial(42) == 43
        assert bump_soa_serial(1) == 2
        assert bump_soa_serial(999999999) == 1000000000

    def test_invalid_date_digits_increments_by_one(self) -> None:
        # 10 цифр, но дата невалидна (месяц 99)
        assert bump_soa_serial(2020990101) == 2020990102

    def test_nine_digit_serial_increments_by_one(self) -> None:
        assert bump_soa_serial(202601001) == 202601002


# ---------------------------------------------------------------------------
# apply_serial_bump — многострочный SOA
# ---------------------------------------------------------------------------

class TestApplySerialBumpMultiline:
    def test_serial_incremented_in_zone_text(self) -> None:
        old_serial = int(_today_prefix() + "03")
        text = _zone(old_serial)
        result = apply_serial_bump(text, ZONE_NAME)
        expected = int(_today_prefix() + "04")
        assert str(expected) in result
        assert str(old_serial) not in result

    def test_other_lines_unchanged(self) -> None:
        text = _zone(2024010101)
        result = apply_serial_bump(text, ZONE_NAME)
        assert "ns1.example.com." in result
        assert "1.2.3.4" in result
        assert "7200" in result

    def test_arbitrary_serial_plus_one(self) -> None:
        text = _zone(12345)
        result = apply_serial_bump(text, ZONE_NAME)
        assert "12346" in result
        assert "12345" not in result


# ---------------------------------------------------------------------------
# apply_serial_bump — однострочный SOA
# ---------------------------------------------------------------------------

class TestApplySerialBumpInline:
    def test_inline_serial_incremented(self) -> None:
        old_serial = int(_today_prefix() + "01")
        text = _zone(old_serial, inline=True)
        result = apply_serial_bump(text, ZONE_NAME)
        expected = int(_today_prefix() + "02")
        assert str(expected) in result

    def test_inline_arbitrary_serial(self) -> None:
        text = _zone(777, inline=True)
        result = apply_serial_bump(text, ZONE_NAME)
        assert "778" in result
        assert " 777 " not in result
