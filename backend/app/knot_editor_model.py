"""Модель редактора knot.conf (MVP): server, include, zone — для формы и API."""

from __future__ import annotations

from typing import Any, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _lines_from_yaml_value(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, list):
        return "\n".join(str(x) for x in val if x is not None and str(x).strip())
    return str(val).strip()


def _yaml_value_from_lines(text: str, *, as_list: bool) -> Any:
    lines = [ln.strip() for ln in text.replace(",", "\n").splitlines() if ln.strip()]
    if not lines:
        return [] if as_list else ""
    if as_list:
        return lines
    return lines[0] if len(lines) == 1 else lines


def _acl_from_yaml(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, list):
        return "\n".join(str(x) for x in val)
    return str(val)


def _acl_to_yaml(text: str) -> list[str]:
    parts: List[str] = []
    for ln in text.replace(",", "\n").splitlines():
        for p in ln.split(","):
            s = p.strip()
            if s:
                parts.append(s)
    return parts


class ZoneFormItem(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    domain: str
    file: str = ""
    master: str = ""  # textarea
    notify: str = ""
    acl: str = ""
    dnssec_signing: str = Field(default="off", alias="dnssec-signing")

    @field_validator("dnssec_signing", mode="before")
    @classmethod
    def _signing(cls, v: Any) -> str:
        s = str(v or "off").lower()
        return s if s in ("on", "off") else "off"


class KnotEditorModel(BaseModel):
    """Снимок для формы; совпадает с JSON API."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    server: dict[str, Any] = Field(default_factory=dict)
    include: str = ""  # многострочный список путей
    zone: list[ZoneFormItem] = Field(default_factory=list)
    form_parse_warning: str | None = Field(
        default=None,
        description="Если не удалось разобрать часть YAML, предупреждение для UI",
    )


def extract_editor_model(root: Any) -> KnotEditorModel:
    """Из корня распарсенного knot.conf (dict-like)."""
    warning: str | None = None
    if not isinstance(root, dict):
        return KnotEditorModel(form_parse_warning="Корень конфига не объект YAML")

    server_raw = root.get("server")
    server: dict[str, Any] = {}
    if isinstance(server_raw, dict):
        for k in ("listen", "identity", "nsid", "automatic-acl"):
            if k in server_raw:
                v = server_raw[k]
                if k == "listen":
                    server[k] = _lines_from_yaml_value(v)
                else:
                    server[k] = v if v is not None else ""

    inc = root.get("include")
    include_text = _lines_from_yaml_value(inc)

    zones_out: list[ZoneFormItem] = []
    z = root.get("zone")
    if z is None:
        pass
    elif not isinstance(z, list):
        warning = (warning + "; " if warning else "") + "Секция zone не список — пропущена"
    else:
        for item in z:
            if not isinstance(item, dict):
                continue
            dom = item.get("domain")
            if not dom:
                continue
            zones_out.append(
                ZoneFormItem(
                    domain=str(dom),
                    file=str(item.get("file") or ""),
                    master=_lines_from_yaml_value(item.get("master")),
                    notify=_lines_from_yaml_value(item.get("notify")),
                    acl=_acl_from_yaml(item.get("acl")),
                    dnssec_signing=str(item.get("dnssec-signing") or "off"),
                )
            )

    return KnotEditorModel(
        server=server,
        include=include_text,
        zone=zones_out,
        form_parse_warning=warning,
    )


def apply_editor_model(root: Any, model: KnotEditorModel) -> dict[str, Any]:
    """
    Возвращает новый dict для сериализации: копия root с подмешанными server/include/zone.
    """
    import copy

    if not isinstance(root, dict):
        root = {}
    out: dict[str, Any] = copy.deepcopy(root)

    # server — только переданные ключи из model.server (не затираем log/database и т.д.)
    srv_in = out.get("server")
    if not isinstance(srv_in, dict):
        srv_in = {}
    else:
        srv_in = dict(srv_in)
    for k, v in model.server.items():
        if v is None or v == "":
            srv_in.pop(k, None)
            continue
        if k == "listen":
            yv = _yaml_value_from_lines(str(v), as_list=True)
            if yv:
                srv_in[k] = yv[0] if len(yv) == 1 else yv
            else:
                srv_in.pop(k, None)
        elif k == "automatic-acl":
            s = str(v).lower()
            if s in ("on", "off"):
                srv_in[k] = s
        else:
            srv_in[k] = v
    out["server"] = srv_in

    # include
    paths = [ln.strip() for ln in model.include.splitlines() if ln.strip()]
    if not paths:
        out.pop("include", None)
    elif len(paths) == 1:
        out["include"] = paths[0]
    else:
        out["include"] = paths

    # zone — полная замена списка из формы (пустые domain пропускаем)
    zlist: list[dict[str, Any]] = []
    for z in model.zone:
        if not z.domain.strip():
            continue
        block: dict[str, Any] = {"domain": z.domain.strip()}
        if z.file.strip():
            block["file"] = z.file.strip()
        m = _yaml_value_from_lines(z.master, as_list=True)
        if m:
            block["master"] = m[0] if len(m) == 1 else m
        n = _yaml_value_from_lines(z.notify, as_list=True)
        if n:
            block["notify"] = n[0] if len(n) == 1 else n
        acl = _acl_to_yaml(z.acl)
        if acl:
            block["acl"] = acl[0] if len(acl) == 1 else acl
        block["dnssec-signing"] = z.dnssec_signing
        zlist.append(block)
    out["zone"] = zlist

    return out
