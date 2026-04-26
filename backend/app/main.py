"""dnsadmin: FastAPI + статический SPA + JWT. Управление knot-config и restart Knot."""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from kubernetes import client, config
from pydantic import BaseModel, Field, model_validator

from app.axfr_fragment_model import AxfrFragmentModel, parse_axfr_fragment, render_axfr_fragment
from app.axfr_secret import axfr_diag_public_dict, generate_tsig_yaml_fragment, read_axfr_secret
from app.dns_probe import knot_probe, query_soa_serial
from app.dnssec_ds import fetch_ds_records_for_zone, zone_is_ru_family
from app.knot_conf import list_zone_dnssec_flags, set_zone_dnssec_signing, zone_declared_in_knot_conf
from app.knot_editor_model import KnotEditorModel, apply_editor_model, extract_editor_model
from app.knot_validate import knot_conf_needs_axfr, run_knotc_conf_check
from app.knot_yaml import load_schema, parse_knot_conf, serialize_knot_conf
from app.zone_editor import apply_serial_bump, form_to_zone_text, validate_zonefile, zone_text_to_form

logger = logging.getLogger(__name__)

NAMESPACE = os.environ.get("NAMESPACE", "dns-knot")
CONFIGMAP_NAME = os.environ.get("KNOT_CONFIGMAP_NAME", "knot-config")
KNOT_DEPLOYMENT = os.environ.get("KNOT_DEPLOYMENT_NAME", "knot")
DEFAULT_ZONE = os.environ.get("DEFAULT_ZONE", "k3s.local")
KNOT_AXFR_SECRET_NAME = os.environ.get("KNOT_AXFR_SECRET_NAME", "knot-axfr")
KNOT_AXFR_SECRET_KEY = os.environ.get("KNOT_AXFR_SECRET_KEY", "axfr.conf")

def _load_knot_instances() -> List[Dict[str, Any]]:
    raw = os.environ.get("KNOT_INSTANCES", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        logger.warning("KNOT_INSTANCES: невалидный JSON, мульти-инстанс отключён")
        return []


KNOT_INSTANCES_LIST: List[Dict[str, Any]] = _load_knot_instances()

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

STATIC_DIR = Path(os.environ.get("STATIC_DIR", "")).resolve() if os.environ.get("STATIC_DIR") else (
    Path(__file__).resolve().parent.parent.parent / "dist"
).resolve()

bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(title="dnsadmin", version="0.4.0", docs_url=None, redoc_url=None)


def get_clients():
    config.load_incluster_config()
    return client.CoreV1Api(), client.AppsV1Api()


def _read_knot_conf_map(core: client.CoreV1Api) -> Dict[str, str]:
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    return dict(cm.data or {})


def _zone_files_from_cm(data: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in data.items() if k.endswith(".zone")}


def _read_axfr_secret(core: client.CoreV1Api) -> str | None:
    st = read_axfr_secret(
        core,
        namespace=NAMESPACE,
        secret_name=KNOT_AXFR_SECRET_NAME,
        secret_key=KNOT_AXFR_SECRET_KEY,
    )
    return st.content


def _axfr_validate_sidecar(
    core: client.CoreV1Api,
    knot_conf: str,
    *,
    axfr_override: str | None,
) -> tuple[str | None, Dict[str, Any]]:
    """Какой YAML AXFR подставить в knotc и сводка для JSON."""
    needs = knot_conf_needs_axfr(knot_conf)
    cluster_st = (
        read_axfr_secret(
            core,
            namespace=NAMESPACE,
            secret_name=KNOT_AXFR_SECRET_NAME,
            secret_key=KNOT_AXFR_SECRET_KEY,
        )
        if needs
        else None
    )
    cluster_block = (
        axfr_diag_public_dict(
            cluster_st,
            namespace=NAMESPACE,
            secret_name=KNOT_AXFR_SECRET_NAME,
            secret_key=KNOT_AXFR_SECRET_KEY,
        )
        if cluster_st is not None
        else None
    )

    hints: list[str] = []
    if not needs:
        return None, {
            "config_includes_knot_path": False,
            "source": "not_required",
            "cluster": None,
            "hints": [],
        }

    if axfr_override is not None:
        if axfr_override.strip():
            src = "override"
            axfr = axfr_override
            hints.append("Для проверки используется axfr_override из запроса (не содержимое Secret из кластера).")
            if cluster_st is not None and cluster_st.code != "ok":
                hints.extend(f"[кластер] {h}" for h in cluster_st.hints)
        else:
            src = "override_empty"
            axfr = None
            hints.append("В запросе передан пустой axfr_override — как будто фрагмента нет.")
            if cluster_st is not None:
                hints.extend(cluster_st.hints)
    else:
        src = "secret"
        axfr = cluster_st.content if cluster_st is not None else None
        if cluster_st is not None and cluster_st.code != "ok":
            hints.extend(cluster_st.hints)

    return axfr, {
        "config_includes_knot_path": True,
        "source": src,
        "cluster": cluster_block,
        "hints": hints,
    }


def _validate_knot_conf_bundle(
    knot_conf: str,
    cm_data: Dict[str, str],
    *,
    axfr_override: str | None = None,
) -> Dict[str, Any]:
    try:
        parse_knot_conf(knot_conf)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "yaml_ok": False,
            "yaml_error": str(e),
            "knotc": None,
            "axfr": None,
        }
    core, _ = get_clients()
    axfr, axfr_info = _axfr_validate_sidecar(core, knot_conf, axfr_override=axfr_override)
    res = run_knotc_conf_check(knot_conf, _zone_files_from_cm(cm_data), axfr_yaml=axfr)
    return {
        "ok": res.ok,
        "yaml_ok": True,
        "yaml_error": None,
        "knotc": {"ran": res.ran, "ok": res.ok, "message": res.message},
        "axfr": axfr_info,
    }


def _apply_knot_conf_and_restart(knot_conf: str) -> Dict[str, Any]:
    core, apps = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    if cm.data is None:
        cm.data = {}
    data = dict(cm.data)
    validation = _validate_knot_conf_bundle(knot_conf, data)
    if not validation["ok"]:
        raise HTTPException(status_code=400, detail=validation)
    data["knot.conf"] = knot_conf
    cm.data = data
    core.patch_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, cm)
    ts = _trigger_knot_restart(apps)
    return {"status": "ok", "restarted_at": ts, "validation": validation}


class ZoneBody(BaseModel):
    content: str


class DnssecSigningBody(BaseModel):
    signing: bool


class ZoneValidateBody(BaseModel):
    content: str


class SoaFormModel(BaseModel):
    ttl: int = Field(default=3600, ge=1, le=2147483647)
    primary_ns: str = ""
    admin_email: str = ""
    serial: int = Field(default=1, ge=0)
    refresh: int = Field(default=7200, ge=1)
    retry: int = Field(default=3600, ge=1)
    expire: int = Field(default=1209600, ge=1)
    minimum: int = Field(default=300, ge=1)


class NsRowModel(BaseModel):
    host: str = ""


class RecordRowModel(BaseModel):
    name: str = "@"
    ttl: int | None = None
    rtype: str = "A"
    value: str = ""


class ZoneEditorFormModel(BaseModel):
    soa: SoaFormModel
    ns: list[NsRowModel] = Field(default_factory=list)
    records: list[RecordRowModel] = Field(default_factory=list)


class LoginBody(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class KnotConfRawBody(BaseModel):
    content: str


class KnotConfValidateBody(BaseModel):
    content: str
    axfr_override: str | None = None


class AxfrPutBody(BaseModel):
    """PUT /api/knot-conf/axfr: либо готовый YAML, либо structured — сервер соберёт YAML."""

    content: str | None = None
    structured: AxfrFragmentModel | None = None

    @model_validator(mode="after")
    def _need_payload(self) -> AxfrPutBody:
        if self.structured is not None:
            return self
        if self.content is not None:
            return self
        raise ValueError("Укажите content или structured")


class TsigGenerateBody(BaseModel):
    """Тело POST /api/knot-conf/axfr/generate-tsig — генерация фрагмента key:/acl: через `keymgr -t`."""

    key_id: str | None = Field(default=None, description="Имя TSIG; если пусто — axfr-<random>")
    with_acl: bool = Field(default=True, description="Добавить пример acl с action: transfer")
    acl_id: str = Field(default="axfr-allowed", description="Идентификатор ACL в сгенерированном фрагменте")


LABEL_RE = re.compile(r"^(?=.{1,63}$)(?!-)[a-zA-Z0-9-]+(?<!-)$")


def validate_zone_name(name: str) -> None:
    n = name.strip().rstrip(".")
    if not n or len(n) > 253:
        raise HTTPException(status_code=400, detail="Некорректное имя зоны")
    if ".." in n or "/" in n or "\\" in n:
        raise HTTPException(status_code=400, detail="Некорректное имя зоны")
    labels = n.split(".")
    for lab in labels:
        if not LABEL_RE.match(lab):
            raise HTTPException(status_code=400, detail="Некорректное имя зоны")


def ensure_zone_in_knot_conf(knot_conf: str, zone_name: str) -> str:
    if re.search(rf"(?m)^\s*-\s*domain:\s*{re.escape(zone_name)}\s*$", knot_conf):
        return knot_conf
    block = (
        f"  - domain: {zone_name}\n"
        f"    file: /zones/{zone_name}.zone\n"
        f"    acl: [axfr-allowed]\n"
        f"    dnssec-signing: off\n"
    )
    return knot_conf.rstrip() + "\n\n" + block + "\n"


def _issue_token(username: str) -> str:
    if not JWT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="JWT_SECRET не задан",
        )
    now = int(dt.datetime.utcnow().timestamp())
    exp = now + JWT_EXPIRE_HOURS * 3600
    payload: Dict[str, Any] = {
        "sub": username,
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Dict[str, Any]:
    if cred is None or cred.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется Bearer-токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not JWT_SECRET:
        raise HTTPException(status_code=500, detail="JWT_SECRET не задан")
    try:
        payload = jwt.decode(cred.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или просроченный токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Неверный токен")
    return payload


@app.on_event("startup")
async def startup():
    if not JWT_SECRET:
        logger.error("JWT_SECRET is not set — auth and /api will fail")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


def _knot_running_pod_ip() -> str | None:
    """
    IP pod Knot (fallback для DNS-проб, если нет KNOT_DNS_PROBE_HOST и нет явного listen в knot.conf).
    """
    try:
        core, _ = get_clients()
        pods = core.list_namespaced_pod(NAMESPACE, label_selector=f"app={KNOT_DEPLOYMENT}")
    except Exception as e:  # noqa: BLE001
        logger.debug("list knot pods for dns target: %s", e)
        return None
    for pod in pods.items or []:
        st = pod.status
        if not st or (st.phase or "").lower() != "running":
            continue
        ip = st.pod_ip or st.host_ip
        if ip:
            return ip
    return None


def _knot_dns_probe_resolution() -> tuple[str, str]:
    """
    (хост, источник) для SOA/DS-проб.

    Приоритет: KNOT_DNS_PROBE_HOST → явный адрес из knot.conf server.listen (не wildcard)
    → IP Running pod Knot → KNOT_DNS_HOST.
    """
    h = os.environ.get("KNOT_DNS_PROBE_HOST", "").strip()
    if h:
        return h, "KNOT_DNS_PROBE_HOST"
    try:
        from app.knot_listen_probe import listen_host_for_dns_probe

        core, _ = get_clients()
        data = _read_knot_conf_map(core)
        raw = data.get("knot.conf") or ""
        lh = listen_host_for_dns_probe(raw)
        if lh:
            return lh, "knot.conf.listen"
    except Exception as e:  # noqa: BLE001
        logger.debug("probe host from knot.conf listen: %s", e)
    ip = _knot_running_pod_ip()
    if ip:
        return ip, "knot_pod_ip"
    return os.environ.get("KNOT_DNS_HOST", "knot"), "KNOT_DNS_HOST"


def _knot_dns_reachable_host() -> str:
    """Куда слать DNS-запросы (SOA/DS)."""
    host, _ = _knot_dns_probe_resolution()
    return host


@app.get("/api/dns-health")
def dns_health(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """SOA (UDP). Цель см. _knot_dns_probe_resolution."""
    host, source = _knot_dns_probe_resolution()
    ok, msg, ms = knot_probe(host)
    port = int(os.environ.get("KNOT_DNS_PORT", "53"))
    return {
        "ok": ok,
        "message": msg,
        "latency_ms": round(ms, 2) if ms is not None else None,
        "probe_host": host,
        "probe_source": source,
        "probe_port": port,
    }


@app.get("/api/knot-conf")
def get_knot_conf(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    core, _ = get_clients()
    data = _read_knot_conf_map(core)
    raw = data.get("knot.conf") or ""
    schema = load_schema()
    return {"raw": raw, "schema_version": str(schema.get("version", "1"))}


@app.get("/api/knot-conf/schema")
def get_knot_conf_schema(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    return load_schema()


@app.get("/api/knot-conf/model")
def get_knot_conf_model(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    core, _ = get_clients()
    raw = (_read_knot_conf_map(core)).get("knot.conf") or ""
    root = parse_knot_conf(raw)
    model = extract_editor_model(root)
    return model.model_dump(mode="json", by_alias=True)


@app.post("/api/knot-conf/validate")
def post_knot_conf_validate(
    body: KnotConfValidateBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    core, _ = get_clients()
    data = _read_knot_conf_map(core)
    return _validate_knot_conf_bundle(body.content, data, axfr_override=body.axfr_override)


@app.post("/api/knot-conf/render-model")
def post_knot_conf_render_model(
    body: Dict[str, Any],
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    """Собрать knot.conf из JSON модели без записи в кластер (для проверки / предпросмотра)."""
    model = KnotEditorModel.model_validate(body)
    core, _ = get_clients()
    raw = (_read_knot_conf_map(core)).get("knot.conf") or ""
    root = parse_knot_conf(raw)
    new_doc = apply_editor_model(root, model)
    return {"content": serialize_knot_conf(new_doc)}


@app.put("/api/knot-conf")
def put_knot_conf_raw(
    body: KnotConfRawBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    return _apply_knot_conf_and_restart(body.content)


@app.put("/api/knot-conf/model")
def put_knot_conf_model(
    body: Dict[str, Any],
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    model = KnotEditorModel.model_validate(body)
    core, _ = get_clients()
    raw = (_read_knot_conf_map(core)).get("knot.conf") or ""
    root = parse_knot_conf(raw)
    new_doc = apply_editor_model(root, model)
    text = serialize_knot_conf(new_doc)
    return _apply_knot_conf_and_restart(text)


@app.get("/api/knot-conf/axfr-status")
def get_axfr_status(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Диагностика Secret AXFR без выдачи содержимого (удобно для подсказок в UI)."""
    core, _ = get_clients()
    st = read_axfr_secret(
        core,
        namespace=NAMESPACE,
        secret_name=KNOT_AXFR_SECRET_NAME,
        secret_key=KNOT_AXFR_SECRET_KEY,
    )
    return axfr_diag_public_dict(
        st,
        namespace=NAMESPACE,
        secret_name=KNOT_AXFR_SECRET_NAME,
        secret_key=KNOT_AXFR_SECRET_KEY,
    )


def _axfr_parse_payload(content: str) -> Dict[str, Any]:
    structured, warn = parse_axfr_fragment(content)
    return {
        "structured": structured.model_dump(mode="json") if structured else None,
        "structured_parse_warning": warn,
    }


def _axfr_structured_response(content: str) -> Dict[str, Any]:
    return {"content": content, **_axfr_parse_payload(content)}


@app.get("/api/knot-conf/axfr")
def get_axfr_fragment(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    core, _ = get_clients()
    st = read_axfr_secret(
        core,
        namespace=NAMESPACE,
        secret_name=KNOT_AXFR_SECRET_NAME,
        secret_key=KNOT_AXFR_SECRET_KEY,
    )
    if st.content is None:
        detail = axfr_diag_public_dict(
            st,
            namespace=NAMESPACE,
            secret_name=KNOT_AXFR_SECRET_NAME,
            secret_key=KNOT_AXFR_SECRET_KEY,
        )
        raise HTTPException(status_code=404, detail=detail)
    return _axfr_structured_response(st.content)


@app.post("/api/knot-conf/axfr/generate-tsig")
def post_axfr_generate_tsig(
    body: TsigGenerateBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Сгенерировать YAML-фрагмент TSIG (keymgr из образа Knot) + пример acl."""
    acl = (body.acl_id or "axfr-allowed").strip()
    if not acl or not LABEL_RE.match(acl):
        raise HTTPException(status_code=400, detail="Некорректный acl_id")
    try:
        yaml_text, kid = generate_tsig_yaml_fragment(
            body.key_id,
            with_acl=body.with_acl,
            acl_id=acl,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    out: Dict[str, Any] = {"yaml": yaml_text, "key_id": kid}
    parsed, warn = parse_axfr_fragment(yaml_text)
    if parsed is not None:
        out["structured"] = parsed.model_dump(mode="json")
    out["structured_parse_warning"] = warn
    return out


class AxfrContentBody(BaseModel):
    content: str = ""


@app.post("/api/knot-conf/axfr/parse-fragment")
def post_axfr_parse_fragment(
    body: AxfrContentBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Разобрать YAML фрагмента (например после правки на вкладке YAML) без чтения Secret."""
    return _axfr_parse_payload(body.content)


@app.post("/api/knot-conf/axfr/render-model")
def post_axfr_render_model(
    body: Dict[str, Any],
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    """Собрать YAML фрагмента AXFR из structured (keys/acls) без записи в кластер."""
    try:
        model = AxfrFragmentModel.model_validate(body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Некорректная structured-модель: {e}") from e
    return {"content": render_axfr_fragment(model)}


@app.put("/api/knot-conf/axfr")
def put_axfr_fragment(
    body: AxfrPutBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    core, apps = get_clients()
    pre = read_axfr_secret(
        core,
        namespace=NAMESPACE,
        secret_name=KNOT_AXFR_SECRET_NAME,
        secret_key=KNOT_AXFR_SECRET_KEY,
    )
    if pre.code == "not_found":
        raise HTTPException(
            status_code=404,
            detail={
                **axfr_diag_public_dict(
                    pre,
                    namespace=NAMESPACE,
                    secret_name=KNOT_AXFR_SECRET_NAME,
                    secret_key=KNOT_AXFR_SECRET_KEY,
                ),
                "help": "Secret ещё не создан — kubectl apply k8s/20-knot-axfr-secret.example.yaml "
                "или create secret, затем повторите сохранение.",
            },
        )
    if pre.code == "forbidden":
        raise HTTPException(
            status_code=403,
            detail={
                **axfr_diag_public_dict(
                    pre,
                    namespace=NAMESPACE,
                    secret_name=KNOT_AXFR_SECRET_NAME,
                    secret_key=KNOT_AXFR_SECRET_KEY,
                ),
                "help": "Нет прав на чтение/запись Secret — проверьте RBAC (k8s/60-dnsadmin-rbac.yaml).",
            },
        )
    data = _read_knot_conf_map(core)
    knot_conf = data.get("knot.conf") or ""
    axfr_yaml = render_axfr_fragment(body.structured) if body.structured is not None else (body.content or "")
    try:
        parse_knot_conf(axfr_yaml)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"YAML axfr не разобран: {e}") from e
    res = run_knotc_conf_check(
        knot_conf,
        _zone_files_from_cm(data),
        axfr_yaml=axfr_yaml,
    )
    if not res.ok:
        raise HTTPException(
            status_code=400,
            detail={"knotc": {"ran": res.ran, "ok": res.ok, "message": res.message}},
        )
    sec = core.read_namespaced_secret(KNOT_AXFR_SECRET_NAME, NAMESPACE)
    if sec.data is None:
        sec.data = {}
    sec.data[KNOT_AXFR_SECRET_KEY] = base64.b64encode(axfr_yaml.encode("utf-8")).decode("ascii")
    if sec.string_data is not None:
        sec.string_data = None
    core.replace_namespaced_secret(KNOT_AXFR_SECRET_NAME, NAMESPACE, sec)
    ts = _trigger_knot_restart(apps)
    return {"status": "ok", "restarted_at": ts, "knotc": {"ran": res.ran, "ok": res.ok, "message": res.message}}


@app.post("/api/auth/login", response_model=TokenResponse)
def login(body: LoginBody) -> TokenResponse:
    if body.username != ADMIN_USERNAME or body.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    return TokenResponse(access_token=_issue_token(body.username))


@app.get("/api/instances")
def get_instances(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Список сконфигурированных Knot-инстансов (из KNOT_INSTANCES)."""
    return {"instances": KNOT_INSTANCES_LIST}


@app.get("/api/zones/sync-status")
def get_zones_sync_status(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Сверка SOA serial по всем зонам на всех инстансах.
    Требует KNOT_INSTANCES. Возвращает матрицу зона × сервер с serial и статусом синхронизации.
    """
    if not KNOT_INSTANCES_LIST:
        return {"instances": [], "zones": [], "warning": "KNOT_INSTANCES не настроен — мульти-инстанс отключён"}

    core, _ = get_clients()
    cm_data = _read_knot_conf_map(core)
    zone_names = sorted(k[:-5] for k in cm_data if k.endswith(".zone"))

    zones_result = []
    for zone in zone_names:
        servers: List[Dict[str, Any]] = []
        primary_serial: int | None = None

        for inst in KNOT_INSTANCES_LIST:
            ip = inst.get("ip", "")
            role = inst.get("role", "unknown")
            ok, serial, msg = query_soa_serial(ip, zone)
            if role == "primary" and serial is not None:
                primary_serial = serial
            servers.append({
                "id": inst.get("id"),
                "label": inst.get("label", inst.get("id")),
                "ip": ip,
                "role": role,
                "ok": ok,
                "serial": serial,
                "message": msg if not ok else None,
            })

        for s in servers:
            if primary_serial is None or s["serial"] is None:
                s["synced"] = None
            else:
                s["synced"] = s["serial"] >= primary_serial

        zones_result.append({"zone": zone, "servers": servers, "primary_serial": primary_serial})

    return {"instances": KNOT_INSTANCES_LIST, "zones": zones_result}


@app.get("/api/zones")
def list_zones(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    core, _ = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    raw = cm.data or {}
    keys = sorted([k[:-5] for k in raw.keys() if k.endswith(".zone")])
    if DEFAULT_ZONE in keys:
        keys.remove(DEFAULT_ZONE)
        keys.insert(0, DEFAULT_ZONE)
    flags = list_zone_dnssec_flags(raw.get("knot.conf") or "")
    zones = [{"name": z, "dnssec_signing": flags.get(z, False)} for z in keys]
    return {"zones": zones}


@app.post("/api/zones/{zone_name}/validate")
def validate_zone(
    zone_name: str,
    body: ZoneValidateBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    validate_zone_name(zone_name)
    ok, errs = validate_zonefile(zone_name, body.content)
    return {"valid": ok, "errors": errs}


@app.post("/api/zones/{zone_name}/parse-form")
def parse_zone_form(
    zone_name: str,
    body: ZoneValidateBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    validate_zone_name(zone_name)
    try:
        form = zone_text_to_form(zone_name, body.content)
        return {"form": form}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/zones/{zone_name}/render-form")
def render_zone_form(
    zone_name: str,
    body: ZoneEditorFormModel,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    validate_zone_name(zone_name)
    try:
        text = form_to_zone_text(zone_name, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    ok, errs = validate_zonefile(zone_name, text)
    if not ok:
        raise HTTPException(status_code=400, detail=errs)
    return {"content": text}


@app.put("/api/zones/{zone_name}/form")
def save_zone_form(
    zone_name: str,
    body: ZoneEditorFormModel,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    validate_zone_name(zone_name)
    try:
        text = form_to_zone_text(zone_name, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _apply_zone_update(zone_name, text)


@app.get("/api/zones/{zone_name}")
def get_zone(zone_name: str, _: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, str]:
    validate_zone_name(zone_name)
    core, _ = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    key = f"{zone_name}.zone"
    data = cm.data or {}
    if key not in data:
        raise HTTPException(status_code=404, detail=f"Zone {zone_name} not found")
    return {"zone": zone_name, "content": data[key]}


def _trigger_knot_restart(apps: client.AppsV1Api) -> str:
    ts = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "dnsadmin/restarted-at": ts,
                    }
                }
            }
        }
    }
    apps.patch_namespaced_deployment(KNOT_DEPLOYMENT, NAMESPACE, patch)
    return ts


def _knotc_zone_notify(zone_name: str) -> bool:
    """Отправляет knotc zone-notify. Возвращает True при успехе, False при ошибке."""
    bin_path = os.environ.get("KNOTC_BIN", "knotc")
    try:
        proc = subprocess.run(
            [bin_path, "zone-notify", zone_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            logger.warning("knotc zone-notify %s failed: %s", zone_name, proc.stderr.strip())
            return False
        return True
    except FileNotFoundError:
        logger.debug("knotc не найден, zone-notify пропущен")
        return False
    except Exception:  # noqa: BLE001
        logger.exception("knotc zone-notify %s: неожиданная ошибка", zone_name)
        return False


@app.patch("/api/zones/{zone_name}/dnssec")
def patch_zone_dnssec(
    zone_name: str,
    body: DnssecSigningBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    validate_zone_name(zone_name)
    core, apps = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    if cm.data is None:
        cm.data = {}
    data = cm.data
    key = f"{zone_name}.zone"
    if key not in data:
        raise HTTPException(status_code=404, detail=f"Зона {zone_name} не найдена")
    knot_conf = data.get("knot.conf")
    if not knot_conf:
        raise HTTPException(status_code=500, detail="В ConfigMap нет knot.conf")
    if not zone_declared_in_knot_conf(knot_conf, zone_name):
        raise HTTPException(
            status_code=400,
            detail="Зона не объявлена в knot.conf — добавьте зону или исправьте конфиг вручную",
        )
    try:
        data["knot.conf"] = set_zone_dnssec_signing(knot_conf, zone_name, body.signing)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    core.patch_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, cm)
    ts = _trigger_knot_restart(apps)
    return {"status": "ok", "restarted_at": ts, "dnssec_signing": body.signing}


@app.get("/api/zones/{zone_name}/dnssec-ds")
def get_zone_dnssec_ds(
    zone_name: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    validate_zone_name(zone_name)
    core, _ = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    raw = cm.data or {}
    flags = list_zone_dnssec_flags(raw.get("knot.conf") or "")
    if not flags.get(zone_name, False):
        raise HTTPException(
            status_code=404,
            detail="Для зоны выключено dnssec-signing в knot.conf",
        )
    host = _knot_dns_reachable_host()
    port = int(os.environ.get("KNOT_DNS_PORT", "53"))
    try:
        ds_list, dnskey_list, msg = fetch_ds_records_for_zone(host, zone_name, port=port)
    except OSError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not ds_list:
        raise HTTPException(status_code=404, detail=msg)
    return {
        "ds": ds_list,
        "dnskey": dnskey_list,
        "message": msg,
        "registrar_ru_family": zone_is_ru_family(zone_name),
    }


def _apply_zone_update(zone_name: str, content: str) -> Dict[str, str]:
    validate_zone_name(zone_name)
    ok, errs = validate_zonefile(zone_name, content)
    if not ok:
        raise HTTPException(status_code=400, detail=errs)
    core, apps = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    if cm.data is None:
        cm.data = {}
    key = f"{zone_name}.zone"
    is_new = key not in cm.data

    existing = cm.data.get(key, "")
    if not is_new and existing.strip() == content.strip():
        return {"status": "no_changes"}

    if not is_new:
        try:
            content = apply_serial_bump(content, zone_name)
        except Exception:  # noqa: BLE001
            pass  # не ломаем сохранение если bump не удался

    cm.data[key] = content
    if is_new and cm.data.get("knot.conf"):
        cm.data["knot.conf"] = ensure_zone_in_knot_conf(cm.data["knot.conf"], zone_name)
    core.patch_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, cm)

    ts = _trigger_knot_restart(apps)
    notify_sent = _knotc_zone_notify(zone_name)
    return {"status": "ok", "restarted_at": ts, "notify_sent": str(notify_sent).lower()}


@app.put("/api/zones/{zone_name}")
def update_zone(
    zone_name: str,
    body: ZoneBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    return _apply_zone_update(zone_name, body.content)


def _install_spa(app: FastAPI) -> None:
    if not STATIC_DIR.is_dir():
        logger.warning("Директория статики не найдена (%s) — только API", STATIC_DIR)
        return
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        logger.warning("Нет index.html в %s — только API", STATIC_DIR)
        return

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def spa_index() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (STATIC_DIR / full_path).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return FileResponse(index)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


_install_spa(app)
