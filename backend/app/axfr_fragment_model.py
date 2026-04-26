"""Структурированная модель фрагмента AXFR (key: / acl:) для UI и API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.knot_yaml import parse_knot_conf, serialize_knot_conf


def _norm_address(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val.strip()] if val.strip() else []
    if isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
        out: list[str] = []
        for x in val:
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    s = str(val).strip()
    return [s] if s else []


def _key_entry_from_mapping(m: Mapping[str, Any]) -> dict[str, Any] | None:
    kid = m.get("id")
    if not kid or not str(kid).strip():
        return None
    row: dict[str, Any] = {"id": str(kid).strip()}
    if m.get("algorithm") is not None:
        row["algorithm"] = str(m["algorithm"])
    if m.get("secret") is not None:
        row["secret"] = str(m["secret"])
    if m.get("storage") is not None:
        row["storage"] = str(m["storage"])
    if m.get("file") is not None:
        row["file"] = str(m["file"])
    return row


def _acl_entry_from_mapping(m: Mapping[str, Any]) -> dict[str, Any] | None:
    aid = m.get("id")
    if not aid or not str(aid).strip():
        return None
    row: dict[str, Any] = {"id": str(aid).strip()}
    if m.get("action") is not None:
        row["action"] = str(m["action"])
    row["address"] = _norm_address(m.get("address"))
    if m.get("key") is not None and str(m.get("key")).strip():
        row["key"] = str(m["key"]).strip()
    return row


class AxfrKeyItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    algorithm: str = "hmac-sha256"
    secret: str = ""
    storage: str | None = None
    file: str | None = None


class AxfrAclItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    action: str = "transfer"
    address: list[str] = Field(default_factory=list)
    key: str | None = None

    @field_validator("key", mode="before")
    @classmethod
    def _key_empty(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        s = str(v).strip()
        return s or None


class AxfrFragmentModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keys: list[AxfrKeyItem] = Field(default_factory=list)
    acls: list[AxfrAclItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_and_refs(self) -> AxfrFragmentModel:
        kid = [k.id for k in self.keys]
        if len(kid) != len(set(kid)):
            raise ValueError("Идентификаторы TSIG (key.id) должны быть уникальны")
        aids = [a.id for a in self.acls]
        if len(aids) != len(set(aids)):
            raise ValueError("Идентификаторы ACL (acl.id) должны быть уникальны")
        key_ids = set(kid)
        for a in self.acls:
            if a.key and a.key not in key_ids:
                raise ValueError(f"ACL «{a.id}» ссылается на неизвестный key «{a.key}»")
        return self


def parse_axfr_fragment(text: str) -> tuple[AxfrFragmentModel | None, str | None]:
    """
    Разобрать YAML фрагмента Secret.

    Возвращает (модель, предупреждение). При фатальной ошибке YAML модель None.
    """
    raw = text.strip()
    if not raw:
        return AxfrFragmentModel(), None

    try:
        root = parse_knot_conf(raw)
    except Exception as e:  # noqa: BLE001
        return None, f"YAML не разобран: {e}"

    if not isinstance(root, Mapping):
        return None, "Корень фрагмента AXFR должен быть объектом YAML"

    warnings: list[str] = []
    extra = [k for k in root if k not in ("key", "acl")]
    if extra:
        warnings.append("Неизвестные верхнеуровневые ключи (не переносятся в форму): " + ", ".join(extra))

    keys_out: list[AxfrKeyItem] = []
    key_raw = root.get("key")
    if key_raw is None:
        pass
    elif isinstance(key_raw, Sequence) and not isinstance(key_raw, (str, bytes)):
        for item in key_raw:
            if not isinstance(item, Mapping):
                warnings.append("Пропущена запись key: не объект")
                continue
            d = _key_entry_from_mapping(item)
            if not d:
                warnings.append("Пропущена запись key: нет id")
                continue
            try:
                keys_out.append(AxfrKeyItem.model_validate(d))
            except Exception as e:  # noqa: BLE001
                warnings.append(f"Запись key: {e}")
    else:
        warnings.append("key: ожидался список")

    acls_out: list[AxfrAclItem] = []
    acl_raw = root.get("acl")
    if acl_raw is None:
        pass
    elif isinstance(acl_raw, Sequence) and not isinstance(acl_raw, (str, bytes)):
        for item in acl_raw:
            if not isinstance(item, Mapping):
                warnings.append("Пропущена запись acl: не объект")
                continue
            d = _acl_entry_from_mapping(item)
            if not d:
                warnings.append("Пропущена запись acl: нет id")
                continue
            try:
                acls_out.append(AxfrAclItem.model_validate(d))
            except Exception as e:  # noqa: BLE001
                warnings.append(f"Запись acl: {e}")
    else:
        warnings.append("acl: ожидался список")

    try:
        model = AxfrFragmentModel(keys=keys_out, acls=acls_out)
    except ValueError as e:
        return None, str(e)

    warn = "; ".join(warnings) if warnings else None
    return model, warn


def render_axfr_fragment(model: AxfrFragmentModel) -> str:
    """Собрать YAML только из key и acl (без комментариев)."""
    doc: dict[str, Any] = {}
    if model.keys:
        klist: list[dict[str, Any]] = []
        for k in model.keys:
            row: dict[str, Any] = {"id": k.id}
            if k.algorithm:
                row["algorithm"] = k.algorithm
            if k.secret:
                row["secret"] = k.secret
            if k.storage:
                row["storage"] = k.storage
            if k.file:
                row["file"] = k.file
            klist.append(row)
        doc["key"] = klist
    if model.acls:
        alist: list[dict[str, Any]] = []
        for a in model.acls:
            row: dict[str, Any] = {"id": a.id, "action": a.action}
            if a.address:
                row["address"] = a.address if len(a.address) > 1 else a.address[0]
            if a.key:
                row["key"] = a.key
            alist.append(row)
        doc["acl"] = alist
    return serialize_knot_conf(doc) if doc else ""
