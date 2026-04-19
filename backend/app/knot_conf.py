"""Разбор и правка knot.conf: блоки zone / dnssec-signing."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

_DOMAIN_LINE = re.compile(r"^(?P<indent>\s*)-\s*domain:\s*(?P<name>\S+)\s*$")
_DNSSEC_LINE = re.compile(r"^(\s*)dnssec-signing:\s*(on|off)\s*$")
_FILE_LINE = re.compile(r"^(\s*)file:\s*\S")


def list_zone_dnssec_flags(knot_conf: str) -> Dict[str, bool]:
    """По каждой зоне в секции zone: — включено ли dnssec-signing (по умолчанию off)."""
    lines = knot_conf.splitlines()
    flags: Dict[str, bool] = {}
    i = 0
    n = len(lines)
    while i < n:
        m = _DOMAIN_LINE.match(lines[i])
        if not m:
            i += 1
            continue
        zone = m.group("name")
        base_indent = len(m.group("indent"))
        signing = False
        i += 1
        while i < n:
            raw = lines[i]
            if not raw.strip():
                i += 1
                continue
            lead = len(raw) - len(raw.lstrip(" "))
            if lead <= base_indent and raw.lstrip().startswith("-"):
                break
            dm = _DNSSEC_LINE.match(raw)
            if dm:
                signing = dm.group(2) == "on"
            i += 1
        flags[zone] = signing
    return flags


def _zone_block_span(lines: List[str], zone_name: str) -> Tuple[int, int]:
    """Индексы [start, end) строк блока `- domain: zone_name` (включая строку domain)."""
    start = -1
    base_indent = -1
    for i, line in enumerate(lines):
        m = _DOMAIN_LINE.match(line)
        if not m:
            continue
        if m.group("name") == zone_name:
            start = i
            base_indent = len(m.group("indent"))
            break
    if start < 0:
        raise ValueError(f"Зона {zone_name!r} не найдена в knot.conf")

    end = start + 1
    while end < len(lines):
        raw = lines[end]
        if not raw.strip():
            end += 1
            continue
        lead = len(raw) - len(raw.lstrip(" "))
        if lead <= base_indent and raw.lstrip().startswith("-"):
            break
        end += 1
    return start, end


def set_zone_dnssec_signing(knot_conf: str, zone_name: str, enabled: bool) -> str:
    """Вставить или заменить строку dnssec-signing в блоке зоны."""
    lines = knot_conf.splitlines(keepends=False)
    start, end = _zone_block_span(lines, zone_name)
    block = lines[start:end]
    dm0 = _DOMAIN_LINE.match(lines[start])
    if not dm0:
        raise ValueError(f"Некорректная строка domain для зоны {zone_name!r}")
    base_indent = len(dm0.group("indent"))

    child_indent: int | None = None
    for ln in block[1:]:
        if not ln.strip():
            continue
        lead = len(ln) - len(ln.lstrip(" "))
        if lead > base_indent:
            child_indent = lead
            break
    if child_indent is None:
        child_indent = base_indent + 2

    new_val = "on" if enabled else "off"
    new_line = f"{' ' * child_indent}dnssec-signing: {new_val}"

    dns_idx = -1
    file_idx = -1
    for j, ln in enumerate(block):
        if _DNSSEC_LINE.match(ln):
            dns_idx = j
        if _FILE_LINE.match(ln):
            file_idx = j

    if dns_idx >= 0:
        block[dns_idx] = new_line
    else:
        insert_at = file_idx + 1 if file_idx >= 0 else 1
        block.insert(insert_at, new_line)

    out = lines[:start] + block + lines[end:]
    joined = "\n".join(out)
    if knot_conf.endswith("\n"):
        return joined + "\n"
    return joined


def zone_declared_in_knot_conf(knot_conf: str, zone_name: str) -> bool:
    try:
        _zone_block_span(knot_conf.splitlines(), zone_name)
        return True
    except ValueError:
        return False
