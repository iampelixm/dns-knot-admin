"""Разбор и правка knot.conf: блоки zone / dnssec-signing / remote / acl."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

_DOMAIN_LINE = re.compile(r"^(?P<indent>\s*)-\s*domain:\s*(?P<name>\S+)\s*$")
_DNSSEC_LINE = re.compile(r"^(\s*)dnssec-signing:\s*(on|off)\s*$")
_FILE_LINE = re.compile(r"^(\s*)file:\s*\S")
_KEY_VALUE_LINE = re.compile(r"^(\s*)(?P<key>master|notify|acl|refresh-min-interval|file|dnssec-signing):\s*(?P<value>.*?)\s*$")


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
        # top-level секция (server:/remote:/acl:/zone: и т.п.) — граница блока
        if lead == 0:
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


def _list_value(items) -> str:
    """Сериализовать список ['a','b'] в строку `[a, b]` для YAML knot.conf."""
    if isinstance(items, str):
        return f"[{items}]" if items else "[]"
    return "[" + ", ".join(str(x) for x in items) + "]"


def _upsert_field(block: List[str], key: str, value: str, child_indent: int, *, after: str | None = None) -> None:
    """Вставить/заменить `key: value` в блоке (списке строк). Если after указан — вставить после него."""
    idx = -1
    for j, ln in enumerate(block):
        m = _KEY_VALUE_LINE.match(ln)
        if m and m.group("key") == key:
            idx = j
            break
    new_line = f"{' ' * child_indent}{key}: {value}"
    if idx >= 0:
        block[idx] = new_line
        return
    if after:
        for j, ln in enumerate(block):
            m = _KEY_VALUE_LINE.match(ln)
            if m and m.group("key") == after:
                block.insert(j + 1, new_line)
                return
    block.append(new_line)


def ensure_zone_in_knot_conf_secondary(
    knot_conf: str,
    zone_name: str,
    *,
    master: str = "primary-remote",
    acl: list[str] | str = "notify-allowed",
    refresh_min_interval: int = 14400,
) -> str:
    """Добавить/обновить зону в knot.conf secondary (master/notify формат).

    Параметризовано: `master` — id remote primary, `acl` — список acl для NOTIFY.
    """
    lines = knot_conf.splitlines(keepends=False)
    try:
        start, end = _zone_block_span(lines, zone_name)
    except ValueError:
        start = -1

    if start < 0:
        block = (
            f"  - domain: {zone_name}\n"
            f"    master: {_list_value(master)}\n"
            f"    acl: {_list_value(acl)}\n"
            f"    refresh-min-interval: {refresh_min_interval}\n"
        )
        return knot_conf.rstrip() + "\n" + block + "\n"

    block = lines[start:end]
    dm0 = _DOMAIN_LINE.match(lines[start])
    child_indent = len(dm0.group("indent")) + 2
    _upsert_field(block, "master", _list_value(master), child_indent)
    _upsert_field(block, "acl", _list_value(acl), child_indent)
    _upsert_field(block, "refresh-min-interval", str(refresh_min_interval), child_indent)
    out = lines[:start] + block + lines[end:]
    joined = "\n".join(out)
    if knot_conf.endswith("\n"):
        return joined + "\n"
    return joined


def ensure_zone_in_knot_conf(
    knot_conf: str,
    zone_name: str,
    *,
    notify: list[str] | str = "master-remote",
    acls: list[str] | None = None,
    dnssec_signing: bool | None = None,
) -> str:
    """Добавить/обновить зону в knot.conf primary.

    `notify` — list id remote secondary; `acls` — список acl для зоны.
    У существующего блока значения file/acl/notify/dnssec сохраняются,
    если соответствующий параметр не передан явно.
    """
    lines = knot_conf.splitlines(keepends=False)
    try:
        start, end = _zone_block_span(lines, zone_name)
    except ValueError:
        start = -1

    existing_dnssec = list_zone_field(knot_conf, zone_name, "dnssec-signing")
    existing_acl = list_zone_field(knot_conf, zone_name, "acl")
    existing_file = list_zone_field(knot_conf, zone_name, "file")

    if acls is None and existing_acl is not None:
        acls = existing_acl if isinstance(existing_acl, list) else [existing_acl]
    if dnssec_signing is None and existing_dnssec is not None:
        dnssec_signing = existing_dnssec == "on"
    file_line = f"file: /zones/{zone_name}.zone"

    if start < 0:
        block_str = (
            f"  - domain: {zone_name}\n"
            f"    {file_line}\n"
            f"    acl: {_list_value(acls if acls is not None else ['axfr-allowed'])}\n"
            f"    notify: {_list_value(notify)}\n"
            f"    dnssec-signing: {'on' if dnssec_signing else 'off'}\n"
        )
        return knot_conf.rstrip() + "\n\n" + block_str + "\n"

    block = lines[start:end]
    existing_file_val = existing_file if isinstance(existing_file, str) else None
    dm0 = _DOMAIN_LINE.match(lines[start])
    child_indent = len(dm0.group("indent")) + 2
    _upsert_field(block, "file", existing_file_val or file_line, child_indent)
    if acls is not None:
        _upsert_field(block, "acl", _list_value(acls), child_indent, after="file")
    _upsert_field(block, "notify", _list_value(notify), child_indent)
    if dnssec_signing is not None:
        _upsert_field(block, "dnssec-signing", "on" if dnssec_signing else "off", child_indent)
    out = lines[:start] + block + lines[end:]
    joined = "\n".join(out)
    if knot_conf.endswith("\n"):
        return joined + "\n"
    return joined


def list_zone_field(knot_conf: str, zone_name: str, field: str) -> str | list | None:
    """Прочитать значение поля (master/acl/notify/...) блока зоны."""
    try:
        start, end = _zone_block_span(knot_conf.splitlines(), zone_name)
    except ValueError:
        return None
    for ln in knot_conf.splitlines(keepends=False)[start:end]:
        m = _KEY_VALUE_LINE.match(ln)
        if m and m.group("key") == field:
            raw = m.group("value").strip()
            if raw.startswith("[") and raw.endswith("]"):
                inner = raw[1:-1].strip()
                if not inner:
                    return []
                return [x.strip() for x in inner.split(",") if x.strip()]
            return raw
    return None


def _top_level_block(lines: List[str], name: str) -> List[str]:
    """Вернуть список строк секции верхнего уровня (name: ...), до следующего top-level ключа."""
    out: List[str] = []
    for ln in lines:
        if out and ln.strip() and not ln.startswith(" ") and not ln.startswith("#"):
            break
        out.append(ln)
    return out


def _collect_top_level_block(lines: List[str], i: int) -> Tuple[int, List[str]]:
    """Собрать блок верхнего уровня начиная с индекса i (секция `name:` и её вложенные строки).

    Возвращает (конечный индекс, строки блока). Блок заканчивается на следующей
    непустой строке без отступа (не включая её).
    """
    start = i
    while i < len(lines):
        ln = lines[i]
        if i > start and ln.strip() and not ln.startswith(" ") and not ln.startswith("#") and ln.strip() != "---":
            break
        i += 1
    return i, lines[start:i]


def _extract_section(lines: List[str], name: str) -> Tuple[List[str], List[str]]:
    """Вынуть секции верхнего уровня `name:` из конфига.

    Возвращает (остаток строк, извлечённые блоки). Используется, чтобы перенести
    remote:/acl:/key: перед блоком zone: (knot 3.4 требует объявления до zone).
    """
    rest: List[str] = []
    extracted: List[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith(f"{name}:") and not ln.startswith(" "):
            end, block = _collect_top_level_block(lines, i)
            i = end
            if i < len(lines) and not lines[i].strip():
                i += 1
            if extracted:
                extracted.append("")
            extracted.extend(block)
        else:
            rest.append(ln)
            i += 1
    return rest, extracted


def ensure_symmetric_remotes(
    knot_conf: str,
    peers: List[Tuple[str, str]],
    *,
    key_id: str = "secondary-01",
) -> str:
    """Добавить в knot.conf секции remote:/acl: для пира (идентификатор, адрес).

    remote:
      - id: peer-<id>
        address: <addr>@53
        key: <key_id>
    acl:
      - id: peer-<id>-notify
        action: notify
        address: [<addr>]
        key: <key_id>

    knot 3.4 требует, чтобы remote:/acl:/key: были объявлены ДО секции zone:
    поэтому существующие секции переносятся перед блоком zone: (или в конец,
    если zone отсутствует). Уже существующие peer-записи не дублируются.
    """
    lines = knot_conf.splitlines(keepends=False)

    current_ids = {
        ln.strip().removeprefix("- id: ")
        for ln in lines
        if ln.strip().startswith("- id: ")
    }

    # вынимаем существующие секции remote:/acl:/key:, чтобы гарантировать их позицию
    extracted: List[str] = []
    for section in ("remote", "acl", "key"):
        lines, _ext = _extract_section(lines, section)
        if _ext:
            if extracted:
                extracted.append("")
            extracted.extend(_ext)

    needs_remote: List[tuple[str, str]] = []
    needs_acl: List[tuple[str, str]] = []
    for pid, addr in peers:
        remote_id = f"peer-{pid}"
        acl_id = f"peer-{pid}-notify"
        if remote_id not in current_ids:
            needs_remote.append((pid, addr))
        if acl_id not in current_ids:
            needs_acl.append((pid, addr))

    if not needs_remote and not needs_acl and not extracted:
        return knot_conf

    new_lines: List[str] = []

    if needs_remote:
        new_lines.append("remote:")
        for pid, addr in needs_remote:
            new_lines.append(f"  - id: peer-{pid}")
            new_lines.append(f"    address: {addr}@53")
            new_lines.append(f"    key: {key_id}")

    if needs_acl:
        new_lines.append("acl:")
        for pid, addr in needs_acl:
            new_lines.append(f"  - id: peer-{pid}-notify")
            new_lines.append(f"    action: notify")
            new_lines.append(f"    address: [{addr}]")
            new_lines.append(f"    key: {key_id}")

    block = "\n".join(new_lines)

    # ищем позицию блока zone (первое вхождение top-level `zone:`)
    zone_idx = -1
    for i, ln in enumerate(lines):
        if ln.strip().startswith("zone:") and not ln.startswith(" "):
            zone_idx = i
            break

    sections_before = "\n".join(extracted).strip()
    if sections_before:
        sections_before += "\n"
    if block:
        sections_before += block + "\n"

    if zone_idx >= 0:
        # убираем пустую строку перед zone, чтобы вставка была аккуратной
        insert_at = zone_idx
        if insert_at > 0 and lines[insert_at - 1].strip() == "":
            lines = lines[: insert_at - 1] + lines[insert_at:]
            insert_at -= 1
        out = lines[:insert_at] + ["", sections_before.rstrip(), ""] + lines[insert_at:]
        return "\n".join(out).strip() + "\n"

    if knot_conf.strip().endswith(":"):
        return knot_conf.rstrip() + "\n\n" + block + "\n"
    return knot_conf.rstrip() + "\n" + block + "\n"


def remove_zone_from_knot_conf(knot_conf: str, zone_name: str) -> str:
    """Удалить блок зоны из knot.conf."""
    pattern = (
        rf"(?m)^\s*-\s*domain:\s*{re.escape(zone_name)}\s*\n"
        rf"(?:^\s+(?!- )\S.*\n?)*"
    )
    result = re.sub(pattern, "", knot_conf)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip() + "\n"
