"""Парсинг и сериализация knot.conf с сохранением комментариев (ruamel.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.default_flow_style = False
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def parse_knot_conf(text: str) -> Any:
    if not text.strip():
        return {}
    y = _yaml()
    return y.load(text) or {}


def serialize_knot_conf(data: Any) -> str:
    y = _yaml()
    from io import StringIO

    buf = StringIO()
    y.dump(data, buf)
    s = buf.getvalue()
    if s and not s.endswith("\n"):
        s += "\n"
    return s


def load_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "knot_schema" / "schema.json"
    import json

    with path.open(encoding="utf-8") as f:
        return json.load(f)
