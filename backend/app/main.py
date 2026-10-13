"""dnsadmin: FastAPI + статический SPA + JWT. Управление knot-config и restart Knot."""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import logging
import os
import re
import subprocess
import tarfile
import time
import traceback
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from kubernetes import client, config
from pydantic import BaseModel, Field, model_validator
from ruamel.yaml import YAML as _YAML

from app import crypto_utils, db as _db
from app.axfr_fragment_model import AxfrFragmentModel, parse_axfr_fragment, render_axfr_fragment
from app.axfr_secret import axfr_diag_public_dict, generate_tsig_yaml_fragment, read_axfr_secret
from app.dns_probe import knot_probe, query_soa_serial
from app.dnssec_ds import fetch_ds_records_for_zone, zone_is_ru_family
from app.knot_conf import list_zone_dnssec_flags, set_zone_dnssec_signing, zone_declared_in_knot_conf
from app.knot_conf import ensure_zone_in_knot_conf, ensure_zone_in_knot_conf_secondary, ensure_symmetric_remotes
from app.knot_conf import list_zone_field, remove_zone_from_knot_conf
from app.knot_editor_model import KnotEditorModel, apply_editor_model, extract_editor_model
from app.knot_validate import knot_conf_needs_axfr, run_knotc_conf_check
from app.knot_yaml import load_schema, parse_knot_conf, serialize_knot_conf
from app.zone_editor import apply_serial_bump, form_to_zone_text, validate_zonefile, zone_text_to_form
from app.settings import Settings as _Settings
from app.routers import tsig_keys as _tsig_keys_router
from app.routers import zones as _zones_router
from app.routers import dns_servers as _dns_servers_router
from app.routers import zone_servers as _zone_servers_router

_settings = _Settings()
logger = logging.getLogger(__name__)

NAMESPACE = os.environ.get("NAMESPACE", "dns-knot")
CONFIGMAP_NAME = os.environ.get("KNOT_CONFIGMAP_NAME", "knot-config")
KNOT_DEPLOYMENT = os.environ.get("KNOT_DEPLOYMENT_NAME", "knot")
DEFAULT_ZONE = os.environ.get("DEFAULT_ZONE", "k3s.local")
KNOT_AXFR_SECRET_NAME = os.environ.get("KNOT_AXFR_SECRET_NAME", "knot-axfr")
KNOT_AXFR_SECRET_KEY = os.environ.get("KNOT_AXFR_SECRET_KEY", "axfr.conf")

TLS_CERT_PATH = os.environ.get("TLS_CERT_PATH", "")
TLS_KEY_PATH = os.environ.get("TLS_KEY_PATH", "")

WEBHOOK_GROUP_NAME = os.environ.get("WEBHOOK_GROUP_NAME") or "dnsadmin.knot.io"

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

app = FastAPI(
    title="dnsadmin",
    version="0.4.12",
    description=(
        "Управление DNS-зонами Knot DNS в Kubernetes. "
        "JWT-аутентификация, редактор зон, управление TSIG-ключами, "
        "синхронизация secondary, DNSSEC, Ingress-мастер, "
        "cert-manager webhook DNS-01."
    ),
    contact={"name": "Alexander Semenov", "url": "https://summer-site.ru"},
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
)
app.include_router(_tsig_keys_router.router)
app.include_router(_zones_router.router)
app.include_router(_dns_servers_router.router)
app.include_router(_zone_servers_router.router)


def get_clients():
    ctx = os.environ.get("KUBECONFIG_CONTEXT")
    try:
        config.load_incluster_config()
    except:
        config.load_kube_config(context=ctx)
    return client.CoreV1Api(), client.AppsV1Api()


def _resolve_instance(instance_id: str | None) -> tuple[str, str]:
    """Return (configmap_name, deployment_name) for the given instance_id."""
    if not instance_id:
        return CONFIGMAP_NAME, KNOT_DEPLOYMENT
    for inst in KNOT_INSTANCES_LIST:
        if inst.get("id") == instance_id:
            cm = inst.get("configmap") or CONFIGMAP_NAME
            dep = inst.get("deployment") or KNOT_DEPLOYMENT
            return cm, dep
    raise HTTPException(status_code=404, detail=f"Инстанс '{instance_id}' не найден")


def _read_knot_conf_map(core: client.CoreV1Api, cm_name: str = CONFIGMAP_NAME) -> Dict[str, str]:
    cm = core.read_namespaced_config_map(cm_name, NAMESPACE)
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


def _apply_knot_conf_and_restart(
    knot_conf: str,
    cm_name: str = CONFIGMAP_NAME,
    deployment_name: str = KNOT_DEPLOYMENT,
) -> Dict[str, Any]:
    core, apps = get_clients()
    cm = core.read_namespaced_config_map(cm_name, NAMESPACE)
    if cm.data is None:
        cm.data = {}
    data = dict(cm.data)
    validation = _validate_knot_conf_bundle(knot_conf, data)
    if not validation["ok"]:
        raise HTTPException(status_code=400, detail=validation)
    data["knot.conf"] = knot_conf
    cm.data = data
    core.patch_namespaced_config_map(cm_name, NAMESPACE, cm)
    ts = _trigger_knot_restart(apps, deployment_name)
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


class RecordRowWithSource(RecordRowModel):
    source: str = "local"


class FollowZoneBody(BaseModel):
    master_zone: str
    soa: SoaFormModel
    ns: list[NsRowModel] = Field(default_factory=list)


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


class WebhookChallengeRequest(BaseModel):
    dnsName: str
    key: str
    zone: str
    type: str = "TXT"


def _find_zone_for_dns_name(dns_name: str, zones: list[str]) -> str | None:
    name = dns_name.strip().rstrip(".")
    for z in sorted(zones, key=len, reverse=True):
        if name == z or name.endswith("." + z):
            return z
    return None


def _webhook_present(core: client.CoreV1Api, zone: str, record_name: str, value: str) -> None:
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    key = f"{zone}.zone"
    data = dict(cm.data or {})
    if key not in data:
        raise HTTPException(status_code=404, detail=f"Zone {zone} not found")
    try:
        form = zone_text_to_form(zone, data[key])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse zone: {e}") from e
    records: list[dict] = form.get("records", [])
    match = next(
        (r for r in records if r.get("name") == record_name and r.get("rtype") == "TXT"),
        None,
    )
    rec: Dict[str, Any] = {"name": record_name, "rtype": "TXT", "value": value, "ttl": 60}
    if match:
        match["value"] = value
        match["ttl"] = 60
    else:
        records.append(rec)
    form["records"] = records
    try:
        text = form_to_zone_text(zone, form)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to render zone: {e}") from e
    _apply_zone_update(zone, text, version_source=None)


def _webhook_cleanup(core: client.CoreV1Api, zone: str, record_name: str) -> None:
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    key = f"{zone}.zone"
    data = dict(cm.data or {})
    if key not in data:
        return
    try:
        form = zone_text_to_form(zone, data[key])
    except Exception:
        return
    records: list[dict] = form.get("records", [])
    filtered = [r for r in records if not (r.get("name") == record_name and r.get("rtype") == "TXT")]
    if len(filtered) == len(records):
        return
    form["records"] = filtered
    try:
        text = form_to_zone_text(zone, form)
    except Exception:
        return
    _apply_zone_update(zone, text, version_source=None)


def _parse_axfr_keys(axfr_yaml: str) -> list[dict[str, str]]:
    try:
        data = _YAML(typ="safe").load(axfr_yaml)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    keys_block = data.get("key")
    if not keys_block or not isinstance(keys_block, list):
        return []
    result: list[dict[str, str]] = []
    for entry in keys_block:
        kid = entry.get("id", "")
        alg = entry.get("algorithm", "hmac-sha256")
        secret = entry.get("secret", "")
        if kid and secret:
            result.append({
                "key_id": kid,
                "algorithm": alg,
                "secret_plaintext": secret,
                "scope": "axfr",
            })
    return result


@app.on_event("startup")
async def startup():
    _db.migrate()
    if not JWT_SECRET:
        logger.error("JWT_SECRET is not set — auth and /api will fail")
    if _settings.dns_backend == "knot_k8s":
        try:
            core, _ = get_clients()
            from app import topology as _topo
            _topo.ensure_seeded(core)
            cm_data = _read_knot_conf_map(core)
            zone_files = {k[:-5]: v for k, v in cm_data.items() if k.endswith(".zone")}
            knot_conf = cm_data.get("knot.conf")
            tsig_keys_data = None
            st = read_axfr_secret(
                core,
                namespace=NAMESPACE,
                secret_name=KNOT_AXFR_SECRET_NAME,
                secret_key=KNOT_AXFR_SECRET_KEY,
            )
            if st.code == "ok" and st.content:
                tsig_keys_data = _parse_axfr_keys(st.content)
            result = _db.startup_backup(zone_files, knot_conf, tsig_keys_data)
            if result["status"] == "ok":
                logger.info(
                    "startup_backup: zones=%d versions=%d tsig_keys=%d",
                    result["zones_imported"],
                    result["versions_imported"],
                    result["tsig_keys_imported"],
                )
        except Exception:
            logger.warning("startup_backup skipped (not in cluster?):\n%s", traceback.format_exc())


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
def get_knot_conf(
    _: Dict[str, Any] = Depends(get_current_user),
    instance: str | None = Query(None),
) -> Dict[str, Any]:
    """Получить сырой knot.conf из ConfigMap + версию схемы."""
    cm_name, _ = _resolve_instance(instance)
    core, _ = get_clients()
    data = _read_knot_conf_map(core, cm_name)
    raw = data.get("knot.conf") or ""
    schema = load_schema()
    return {"raw": raw, "schema_version": str(schema.get("version", "1"))}


@app.get("/api/knot-conf/schema")
def get_knot_conf_schema(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Получить схему knot.conf (все секции/поля)."""
    return load_schema()


@app.get("/api/knot-conf/model")
def get_knot_conf_model(
    _: Dict[str, Any] = Depends(get_current_user),
    instance: str | None = Query(None),
) -> Dict[str, Any]:
    """Получить knot.conf в виде структурированной JSON-модели для редактора."""
    cm_name, _ = _resolve_instance(instance)
    core, _ = get_clients()
    raw = (_read_knot_conf_map(core, cm_name)).get("knot.conf") or ""
    root = parse_knot_conf(raw)
    model = extract_editor_model(root)
    return model.model_dump(mode="json", by_alias=True)


@app.post("/api/knot-conf/validate")
def post_knot_conf_validate(
    body: KnotConfValidateBody,
    _: Dict[str, Any] = Depends(get_current_user),
    instance: str | None = Query(None),
) -> Dict[str, Any]:
    """Проверить knot.conf синтаксисом knotc conf-check + AXFR-sidecar."""
    cm_name, _ = _resolve_instance(instance)
    core, _ = get_clients()
    data = _read_knot_conf_map(core, cm_name)
    return _validate_knot_conf_bundle(body.content, data, axfr_override=body.axfr_override)


@app.post("/api/knot-conf/render-model")
def post_knot_conf_render_model(
    body: Dict[str, Any],
    _: Dict[str, Any] = Depends(get_current_user),
    instance: str | None = Query(None),
) -> Dict[str, str]:
    """Собрать knot.conf из JSON модели без записи в кластер (для проверки / предпросмотра)."""
    model = KnotEditorModel.model_validate(body)
    cm_name, _ = _resolve_instance(instance)
    core, _ = get_clients()
    raw = (_read_knot_conf_map(core, cm_name)).get("knot.conf") or ""
    root = parse_knot_conf(raw)
    new_doc = apply_editor_model(root, model)
    return {"content": serialize_knot_conf(new_doc)}


@app.put("/api/knot-conf")
def put_knot_conf_raw(
    body: KnotConfRawBody,
    _: Dict[str, Any] = Depends(get_current_user),
    instance: str | None = Query(None),
) -> Dict[str, Any]:
    """Сохранить knot.conf в ConfigMap и перезапустить Knot."""
    cm_name, dep_name = _resolve_instance(instance)
    return _apply_knot_conf_and_restart(body.content, cm_name, dep_name)


@app.put("/api/knot-conf/model")
def put_knot_conf_model(
    body: Dict[str, Any],
    _: Dict[str, Any] = Depends(get_current_user),
    instance: str | None = Query(None),
) -> Dict[str, Any]:
    """Сохранить knot.conf из JSON-модели редактора в ConfigMap и перезапустить Knot."""
    cm_name, dep_name = _resolve_instance(instance)
    model = KnotEditorModel.model_validate(body)
    core, _ = get_clients()
    raw = (_read_knot_conf_map(core, cm_name)).get("knot.conf") or ""
    root = parse_knot_conf(raw)
    new_doc = apply_editor_model(root, model)
    text = serialize_knot_conf(new_doc)
    return _apply_knot_conf_and_restart(text, cm_name, dep_name)


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
    """Прочитать AXFR Secret и разобрать YAML в structured-модель."""
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
    """Аутентификация администратора.

    Возвращает JWT-токен (Bearer) для доступа к API.
    Все остальные эндпоинты требуют заголовок `Authorization: Bearer <token>`.
    Токен живёт 24 часа (настраивается через JWT_EXPIRE_HOURS).
    """
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
    Сверка SOA serial по всем зонам по пер-доменным ролям (server_id → роль).

    Использует dns_servers + zone_servers (fallback на KNOT_INSTANCES).
    ```json
    {
      "instances": [...],
      "zones": [{
        "zone": "summer-site.ru",
        "servers": [{"id": "first", "role": "primary", "serial": ..., "synced": true, ...}],
        "primary_serial": ...
      }]
    }
    ```
    """
    from app import topology as _topo

    core, _ = get_clients()
    cm_data = _read_knot_conf_map(core)
    zone_names = sorted(k[:-5] for k in cm_data if k.endswith(".zone"))

    if not zone_names:
        return {"instances": _topo.global_instances(), "zones": [], "warning": "Нет зон в primary ConfigMap"}

    # Матрица «какие серверы должны обслуживать зону» (пер-доменные роли)
    zones_result = []
    for zone in zone_names:
        roles = _topo.get_zone_roles(zone)
        primary_id = roles.get("primary")
        secondary_ids = roles.get("secondaries", [])
        server_ids = [sid for sid in [primary_id] + secondary_ids if sid]

        servers: List[Dict[str, Any]] = []
        primary_serial: int | None = None
        for sid in server_ids:
            ip = _topo.server_ip(sid)
            if not ip:
                continue
            role = "primary" if sid == primary_id else "secondary"
            ok, serial, msg = query_soa_serial(ip, zone)
            if role == "primary" and serial is not None:
                primary_serial = serial
            servers.append({
                "id": sid,
                "label": sid,
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

    return {"instances": _topo.global_instances(), "zones": zones_result}


@app.post("/api/zones/{zone_name}/validate")
def validate_zone(
    zone_name: str,
    body: ZoneValidateBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Проверить zone-файл синтаксисом `knotc zone-check`."""
    validate_zone_name(zone_name)
    ok, errs = validate_zonefile(zone_name, body.content)
    return {"valid": ok, "errors": errs}


@app.post("/api/zones/{zone_name}/parse-form")
def parse_zone_form(
    zone_name: str,
    body: ZoneValidateBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Разобрать zone-файл в форму редактора (список записей)."""
    validate_zone_name(zone_name)
    try:
        follower_of = _db.is_follower(zone_name)
        form = zone_text_to_form(zone_name, body.content)

        if follower_of:
            db_records = _db.list_zone_records(zone_name)
            master_ids = {r["id"] for r in db_records if r.get("source") == "master"}
            records_with_source = []
            for rec in form.get("records", []):
                is_master = False
                for dr in db_records:
                    if (dr["name"] == rec.get("name", "@")
                            and dr["rtype"] == rec.get("rtype", "A")
                            and dr["value"] == rec.get("value", "")
                            and dr.get("source") == "master"
                            and dr["master_rec_id"] is not None):
                        is_master = True
                        break
                records_with_source.append({**rec, "source": "master" if is_master else "local"})
            form["records"] = records_with_source

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
    """Собрать zone-файл из формы редактора (без сохранения в кластер)."""
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
) -> Dict[str, Any]:
    """Сохранить зону из формы редактора (serial bump + рестарт Knot).

    Для обычных зон: сохраняет records в zone_records и пропагирует изменения на follower.
    Для follower-зон: сохраняет только local records, объединяет с master records из БД.
    """
    validate_zone_name(zone_name)
    follower_of = _db.is_follower(zone_name)

    save_record_list = [r.model_dump() for r in body.records]

    if follower_of:
        _db.replace_records_from_form(zone_name, save_record_list, source="local")
        master_recs = _db.list_zone_records(follower_of, source="local")
        local_recs = _db.list_zone_records(zone_name, source="local")
        combined = master_recs + local_recs
        combined_form = {
            "soa": body.soa.model_dump(),
            "ns": [n.model_dump() for n in body.ns],
            "records": [
                {"name": r["name"], "ttl": r["ttl"], "rtype": r["rtype"], "value": r["value"]}
                for r in combined
            ],
        }
        try:
            text = form_to_zone_text(zone_name, combined_form)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        result = _apply_zone_update(zone_name, text)
        return result

    _db.replace_records_from_form(zone_name, save_record_list, source="local")

    try:
        text = form_to_zone_text(zone_name, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    result = _apply_zone_update(zone_name, text)

    _sync_followers(zone_name)

    return result


def _sync_followers(master_zone: str):
    """Пропагировать изменения master-зоны на все follower."""
    follower_names = _db.get_followers(master_zone)
    if not follower_names:
        return

    master_recs = _db.list_zone_records(master_zone, source="local")
    master_zone_data = _db.get_zone(master_zone)
    if not master_zone_data:
        return

    for fol_name in follower_names:
        fol_zone_data = _db.get_zone(fol_name)
        if not fol_zone_data:
            continue

        _db.replace_records_from_form(fol_name, [
            {"name": r["name"], "ttl": r["ttl"], "rtype": r["rtype"],
             "value": r["value"], "master_rec_id": r["id"]}
            for r in master_recs
        ], source="master")

        local_recs = _db.list_zone_records(fol_name, source="local")
        combined = master_recs + local_recs
        combined_form = {
            "soa": {
                "ttl": 3600, "primary_ns": "", "admin_email": "",
                "serial": 1, "refresh": 7200, "retry": 3600,
                "expire": 1209600, "minimum": 300,
            },
            "ns": [],
            "records": [
                {"name": r["name"], "ttl": r["ttl"], "rtype": r["rtype"], "value": r["value"]}
                for r in combined
            ],
        }

        latest = _db.get_latest_version(fol_name)
        if latest:
            try:
                existing_form = zone_text_to_form(fol_name, latest["content"])
                combined_form["soa"] = {
                    "ttl": existing_form["soa"]["ttl"],
                    "primary_ns": existing_form["soa"]["primary_ns"],
                    "admin_email": existing_form["soa"]["admin_email"],
                    "serial": existing_form["soa"]["serial"],
                    "refresh": existing_form["soa"]["refresh"],
                    "retry": existing_form["soa"]["retry"],
                    "expire": existing_form["soa"]["expire"],
                    "minimum": existing_form["soa"]["minimum"],
                }
                combined_form["ns"] = existing_form["ns"]
            except Exception:
                pass

        try:
            text = form_to_zone_text(fol_name, combined_form)
            _apply_zone_update(fol_name, text)
        except Exception as e:
            logger.warning("Не удалось синхронизировать follower %s с master %s: %s",
                          fol_name, master_zone, e)


@app.post("/api/zones/{zone_name}/follow")
def follow_zone(
    zone_name: str,
    body: FollowZoneBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    """Создать zone_name как follower для master_zone."""
    validate_zone_name(zone_name)
    validate_zone_name(body.master_zone)

    if zone_name == body.master_zone:
        raise HTTPException(status_code=400, detail="Зона не может быть ведомой для самой себя")

    master = _db.get_zone(body.master_zone)
    if not master or master.get("deleted"):
        raise HTTPException(status_code=404, detail="Мастер-зона не найдена")

    existing = _db.get_zone(zone_name)
    if existing and not existing.get("deleted"):
        raise HTTPException(status_code=400, detail="Зона уже существует")

    form_dict = {
        "soa": body.soa.model_dump(),
        "ns": [n.model_dump() for n in body.ns],
        "records": [],
    }
    try:
        text = form_to_zone_text(zone_name, form_dict)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _db.set_follower(zone_name, body.master_zone)

    result = _apply_zone_update(zone_name, text)

    _sync_followers(body.master_zone)

    return result


@app.post("/api/zones/{zone_name}/unfollow")
def unfollow_zone(
    zone_name: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    """Отвязать follower от master: follower_of = NULL, все записи → local."""
    validate_zone_name(zone_name)
    follower_of = _db.is_follower(zone_name)
    if not follower_of:
        raise HTTPException(status_code=400, detail="Зона не является ведомой")

    _db.set_follower(zone_name, None)
    conn = _db.get_db()
    conn.execute(
        "UPDATE zone_records SET source = 'local', master_rec_id = NULL "
        "WHERE zone_name = ? AND source = 'master'",
        (zone_name,),
    )
    conn.commit()
    conn.close()

    return {"status": "unfollowed"}


@app.get("/api/zones/{zone_name}/followers")
def get_followers(
    zone_name: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, list[str]]:
    """Получить список follower-зон для указанной зоны."""
    validate_zone_name(zone_name)
    followers = _db.get_followers(zone_name)
    return {"followers": followers}


@app.get("/api/zones/{zone_name}/follower-info")
def get_follower_info(
    zone_name: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str | None]:
    """Получить информацию о follower: follower_of (или null)."""
    validate_zone_name(zone_name)
    follower_of = _db.is_follower(zone_name)
    return {"zone": zone_name, "follower_of": follower_of}


@app.get("/api/zones/{zone_name}/content")
def get_zone(zone_name: str, _: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, str]:
    """Получить zone-файл из ConfigMap."""
    validate_zone_name(zone_name)
    core, _ = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    key = f"{zone_name}.zone"
    data = cm.data or {}
    if key not in data:
        raise HTTPException(status_code=404, detail=f"Zone {zone_name} not found")
    return {"zone": zone_name, "content": data[key]}


def _trigger_knot_restart(apps: client.AppsV1Api, deployment_name: str = KNOT_DEPLOYMENT) -> str:
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
    apps.patch_namespaced_deployment(deployment_name, NAMESPACE, patch)
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
    """Включить/отключить DNSSEC-подписание для зоны (dnssec-signing в knot.conf)."""
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


@app.post("/api/zones/{zone_name}/dnssec/init")
def init_zone_dnssec(
    zone_name: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Включить DNSSEC для зоны: dnssec-signing + рестарт Knot + получение DS.

    После включения Knot автоматически генерирует KSK/ZSK.
    Эндпоинт возвращает DS/DNSKEY записи после рестарта.
    """
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
            detail="Зона не объявлена в knot.conf — создайте зону перед включением DNSSEC",
        )
    if list_zone_dnssec_flags(knot_conf).get(zone_name, False):
        raise HTTPException(status_code=409, detail="DNSSEC уже включён для этой зоны")

    try:
        data["knot.conf"] = set_zone_dnssec_signing(knot_conf, zone_name, True)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    core.patch_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, cm)
    ts = _trigger_knot_restart(apps)

    host = _knot_dns_reachable_host()
    port = int(os.environ.get("KNOT_DNS_PORT", "53"))
    import time
    time.sleep(2)
    try:
        ds_list, dnskey_list, msg = fetch_ds_records_for_zone(host, zone_name, port=port)
    except OSError as e:
        ds_list, dnskey_list, msg = [], [], str(e)

    return {
        "status": "ok",
        "restarted_at": ts,
        "dnssec_signing": True,
        "ds": ds_list or None,
        "dnskey": dnskey_list or None,
        "ds_message": msg,
    }


@app.get("/api/zones/export")
def export_zones(
    _: Dict[str, Any] = Depends(get_current_user),
):
    """Скачать все zone-файлы + knot.conf одним tgz-архивом."""
    core = client.CoreV1Api()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    if cm.data is None:
        raise HTTPException(status_code=404, detail="ConfigMap пуст")
    data = cm.data
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        knot_conf = data.get("knot.conf", "")
        if knot_conf:
            info = tarfile.TarInfo(name="knot.conf")
            info.size = len(knot_conf.encode())
            tar.addfile(info, io.BytesIO(knot_conf.encode()))
        for key, content in data.items():
            if key == "knot.conf" or not key.endswith(".zone"):
                continue
            info = tarfile.TarInfo(name=key)
            info.size = len(content.encode())
            tar.addfile(info, io.BytesIO(content.encode()))
    buf.seek(0)
    return Response(content=buf.getvalue(), media_type="application/gzip",
                    headers={"Content-Disposition": "attachment; filename=zones-export.tgz"})


@app.post("/api/zones/import")
def import_zones(
    file: UploadFile = File(...),
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Загрузить tgz-архив с zone-файлами и knot.conf, импортировать в ConfigMap."""
    core = client.CoreV1Api()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    if cm.data is None:
        cm.data = {}
    data = cm.data
    imported = 0
    new_knot_conf = None
    try:
        with tarfile.open(fileobj=file.file, mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    content = tar.extractfile(member).read().decode()
                    if member.name == "knot.conf":
                        new_knot_conf = content
                    elif member.name.endswith(".zone"):
                        zone_name = member.name[:-5]
                        validate_zone_name(zone_name)
                        data[member.name] = content
                        imported += 1
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка импорта: {e}")

    if new_knot_conf is not None:
        data["knot.conf"] = new_knot_conf
    elif imported > 0:
        raise HTTPException(status_code=400, detail="В архиве нет knot.conf")

    core.patch_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, cm)
    ts = _trigger_knot_restart(client.AppsV1Api())
    return {"status": "ok", "zones_imported": imported, "restarted_at": ts}


@app.get("/api/zones/{zone_name}/dnssec-ds")
def get_zone_dnssec_ds(
    zone_name: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Получить DS/DNSKEY записи для делегирования зоны (проверка по DNS)."""
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


def _peer_tsig_key() -> str:
    return os.environ.get("KNOT_PEER_TSIG_KEY", "secondary-01")


def _sync_zone_to_peers(zone_name: str, core: client.CoreV1Api, apps: client.AppsV1Api) -> list[str]:
    """Добавить/обновить зону в knot.conf всех in-cluster secondary по пер-доменным ролям.
    Возвращает список ID серверов, которые были перезапущены."""
    from app import topology as _topo

    restarted: list[str] = []
    roles = _topo.get_zone_roles(zone_name)
    primary_id = roles.get("primary")

    for target in _topo.zone_secondary_targets(zone_name):
        cm_name = target.get("configmap", "")
        dep_name = target.get("deployment", "")
        if not cm_name or not dep_name:
            continue
        try:
            sec_cm = core.read_namespaced_config_map(cm_name, NAMESPACE)
            if sec_cm.data is None:
                sec_cm.data = {}
            sec_conf = sec_cm.data.get("knot.conf", "")
            if not sec_conf:
                continue
            # remote на primary + acl для NOTIFY от primary
            if primary_id:
                p_ip = _topo.server_ip(primary_id)
                if p_ip:
                    sec_conf = ensure_symmetric_remotes(sec_conf, [(primary_id, p_ip)], key_id=_peer_tsig_key())
            new_conf = ensure_zone_in_knot_conf_secondary(
                sec_conf,
                zone_name,
                master=f"peer-{primary_id}" if primary_id else "primary-remote",
                acl=[f"peer-{primary_id}-notify"] if primary_id else "notify-allowed",
            )
            if new_conf == sec_conf:
                continue
            sec_cm.data["knot.conf"] = new_conf
            core.patch_namespaced_config_map(cm_name, NAMESPACE, sec_cm)
            _trigger_knot_restart(apps, dep_name)
            restarted.append(target.get("server_id", cm_name))
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось синхронизировать зону %s на сервер %s", zone_name, target.get("server_id", cm_name))
    return restarted


def _remove_zone_from_peers(zone_name: str, core: client.CoreV1Api, apps: client.AppsV1Api) -> list[str]:
    """Удалить зону из knot.conf всех in-cluster secondary по пер-доменным ролям.
    Возвращает список ID серверов, которые были перезапущены."""
    from app import topology as _topo

    restarted: list[str] = []
    for target in _topo.zone_secondary_targets(zone_name):
        cm_name = target.get("configmap", "")
        dep_name = target.get("deployment", "")
        if not cm_name or not dep_name:
            continue
        try:
            sec_cm = core.read_namespaced_config_map(cm_name, NAMESPACE)
            if sec_cm.data is None:
                continue
            sec_conf = sec_cm.data.get("knot.conf", "")
            if not sec_conf:
                continue
            new_conf = remove_zone_from_knot_conf(sec_conf, zone_name)
            if new_conf == sec_conf:
                continue
            sec_cm.data["knot.conf"] = new_conf
            core.patch_namespaced_config_map(cm_name, NAMESPACE, sec_cm)
            _trigger_knot_restart(apps, dep_name)
            restarted.append(target.get("server_id", cm_name))
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось удалить зону %s с сервера %s", zone_name, target.get("server_id", cm_name))
    return restarted


def _reconcile_zone_roles(
    zone_name: str,
    core: client.CoreV1Api,
    apps: client.AppsV1Api,
) -> Dict[str, Any]:
    """Привести knot.conf всех in-cluster серверов в соответствие с ролями зоны.

    - primary: peer-remotes + notify (не трогаем если зона вообще не в его ConfigMap)
    - secondary: block зоны с master=peer-<primary>
    - прочие in-cluster серверы: блок зоны убирается.

    Возвращает {updated: [server_id, ...], restarted_at}.
    """
    from app import topology as _topo

    roles = _topo.get_zone_roles(zone_name)
    primary_id = roles.get("primary")
    secondary_ids = set(roles.get("secondaries", []))

    updated: list[str] = []
    restarted = ""
    zone_key = f"{zone_name}.zone"

    # Конфигурация primary
    p_cm = None
    if primary_id:
        p_target = _topo.zone_primary_target(zone_name)
        if p_target:
            p_cm = p_target["configmap"]

    # Обновляем knot.conf на всех in-cluster серверах
    for target in _topo.all_in_cluster_targets():
        sid = target["server_id"]
        cm_name = target["configmap"]
        dep_name = target["deployment"]
        try:
            sec_cm = core.read_namespaced_config_map(cm_name, NAMESPACE)
        except Exception:  # noqa: BLE001
            logger.warning("reconcile: не могу прочитать ConfigMap %s", cm_name)
            continue
        if sec_cm.data is None:
            sec_cm.data = {}

        # Если зоны нет в этом ConfigMap — пропускаем (оставляем как есть, тут нет конфигурации зоны)
        has_zone = zone_key in sec_cm.data

        knot_conf = sec_cm.data.get("knot.conf", "")
        new_conf = knot_conf
        if sid == primary_id:
            # primary: если зона есть в его ConfigMap — peer-remotes + notify
            if has_zone and knot_conf:
                new_conf = _ensure_primary_notify_and_peers(knot_conf, zone_name)
        elif sid in secondary_ids:
            # secondary: remote на primary + блок зоны
            if primary_id:
                p_ip = _topo.server_ip(primary_id)
                if p_ip:
                    new_conf = ensure_symmetric_remotes(
                        new_conf, [(primary_id, p_ip)], key_id=_peer_tsig_key(),
                    )
            if has_zone or knot_conf:
                new_conf = ensure_zone_in_knot_conf_secondary(
                    new_conf,
                    zone_name,
                    master=f"peer-{primary_id}" if primary_id else "primary-remote",
                    acl=[f"peer-{primary_id}-notify"] if primary_id else "notify-allowed",
                )
        else:
            # не участвует в зоне: убрать блок из knot.conf
            if knot_conf and zone_declared_in_knot_conf(knot_conf, zone_name):
                new_conf = remove_zone_from_knot_conf(knot_conf, zone_name)

        if new_conf != knot_conf:
            sec_cm.data["knot.conf"] = new_conf
            try:
                core.patch_namespaced_config_map(cm_name, NAMESPACE, sec_cm)
                updated.append(sid)
                ts = _trigger_knot_restart(apps, dep_name) if dep_name else ""
                if ts:
                    restarted = ts
            except Exception:  # noqa: BLE001
                logger.warning("reconcile: не удалось обновить %s", cm_name, exc_info=True)

    return {"updated": updated, "restarted_at": restarted}


def _ensure_primary_notify_and_peers(
    knot_conf: str,
    zone_name: str,
) -> str:
    """Проставить в knot.conf primary: peer-remotes под каждую in-cluster secondary зоны
    и notify-список блока зоны в виде `peer-<secondary_id>`."""
    from app import topology as _topo

    roles = _topo.get_zone_roles(zone_name)
    secondaries = _topo.zone_secondary_targets(zone_name)
    notify_ids: list[str] = []
    for t in secondaries:
        sid = t.get("server_id")
        if not sid:
            continue
        ip = _topo.server_ip(sid)
        if ip:
            knot_conf = ensure_symmetric_remotes(knot_conf, [(sid, ip)], key_id=_peer_tsig_key())
        notify_ids.append(f"peer-{sid}")
    if not notify_ids and roles.get("primary"):
        # fallback: если вторичек нет в per-zone, сохранить старый master-remote
        notify_ids = ["master-remote"]
    return ensure_zone_in_knot_conf(knot_conf, zone_name, notify=notify_ids)


def _apply_zone_update(
    zone_name: str,
    content: str,
    *,
    version_source: str | None = "api",
) -> Dict[str, str]:
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
            pass

    cm.data[key] = content
    if cm.data.get("knot.conf"):
        cm.data["knot.conf"] = _ensure_primary_notify_and_peers(cm.data["knot.conf"], zone_name)
    core.patch_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, cm)

    ts = _trigger_knot_restart(apps)

    synced = _sync_zone_to_peers(zone_name, core, apps)

    notify_sent = _knotc_zone_notify(zone_name)

    result: Dict[str, str] = {
        "status": "ok",
        "restarted_at": ts,
        "notify_sent": str(notify_sent).lower(),
        "synced_secondaries": synced,
    }

    if version_source is not None:
        serial = _db._extract_serial(content)
        checksum = _db.sha256sum(content)
        last = _db.get_latest_version(zone_name)
        if last is None or last["checksum"] != checksum:
            vid = _db.create_version(zone_name, content, serial, checksum, source=version_source)
            result["version_id"] = str(vid)

    return result


def _delete_zone_from_knot(
    zone_name: str,
    core: client.CoreV1Api,
    apps: client.AppsV1Api,
) -> Dict[str, Any]:
    """Удалить зону из ConfigMap и knot.conf primary + secondary. Рестартнуть Knot."""
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    if cm.data is None:
        cm.data = {}
    key = f"{zone_name}.zone"
    if key not in cm.data:
        return {"status": "zone_not_found_in_cm"}

    del cm.data[key]

    knot_conf = cm.data.get("knot.conf", "")
    if knot_conf:
        cm.data["knot.conf"] = remove_zone_from_knot_conf(knot_conf, zone_name)

    core.patch_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, cm)
    ts = _trigger_knot_restart(apps)

    removed = _remove_zone_from_peers(zone_name, core, apps)

    return {
        "status": "ok",
        "restarted_at": ts,
        "removed_from_secondaries": removed,
    }


class UpsertRecordBody(BaseModel):
    name: str
    rtype: str = "A"
    value: str
    ttl: int | None = None


@app.post("/api/zones/{zone_name}/upsert-record")
def upsert_zone_record(
    zone_name: str,
    body: UpsertRecordBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    """Добавить или обновить одну запись в зоне (по имени + типу).

    Автоматически:
    - serial bump
    - рестарт primary Knot
    - синхронизация secondary ConfigMap (для новых зон)
    - zone-notify
    """
    validate_zone_name(zone_name)
    core, _ = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    key = f"{zone_name}.zone"
    data = dict(cm.data or {})
    if key not in data:
        raise HTTPException(status_code=404, detail=f"Зона {zone_name} не найдена")
    try:
        form = zone_text_to_form(zone_name, data[key])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Не удалось разобрать zone-файл: {e}") from e
    records: list[dict] = form.get("records", [])
    match = next(
        (r for r in records if r.get("name") == body.name and r.get("rtype") == body.rtype),
        None,
    )
    if match:
        match["value"] = body.value
        if body.ttl is not None:
            match["ttl"] = body.ttl
    else:
        rec: Dict[str, Any] = {"name": body.name, "rtype": body.rtype, "value": body.value}
        if body.ttl is not None:
            rec["ttl"] = body.ttl
        records.append(rec)
    form["records"] = records
    try:
        text = form_to_zone_text(zone_name, form)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Не удалось сформировать zone-файл: {e}") from e
    return _apply_zone_update(zone_name, text)


@app.put("/api/zones/{zone_name}")
def update_zone(
    zone_name: str,
    body: ZoneBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    """Сохранить zone-файл.

    Автоматически добавляется в knot.conf primary и всех secondary.
    Выполняется serial bump, перезапуск Knot, zone-notify и синхронизация secondary.
    """
    return _apply_zone_update(zone_name, body.content)


# ---------------------------------------------------------------------------
# Sync — принудительная синхронизация зон на secondary
# ---------------------------------------------------------------------------


class ZoneSyncResult(BaseModel):
    zone: str
    synced_secondaries: list[str]
    skipped: list[str]


@app.post("/api/zones/{zone_name}/sync")
def sync_zone(
    zone_name: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ZoneSyncResult:
    """Принудительная синхронизация зоны на все in-cluster secondary по пер-доменным ролям.

    Обновляет блок зоны в knot.conf каждого вторичного ConfigMap,
    если его там нет, и перезапускает соответствующие Knot-деплойменты.
    """
    validate_zone_name(zone_name)
    core, apps = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    key = f"{zone_name}.zone"
    if cm.data is None or key not in cm.data:
        raise HTTPException(status_code=404, detail=f"Зона {zone_name} не найдена в primary ConfigMap")

    from app import topology as _topo

    synced: list[str] = []
    skipped: list[str] = []
    roles = _topo.get_zone_roles(zone_name)
    primary_id = roles.get("primary")

    for target in _topo.zone_secondary_targets(zone_name):
        cm_name = target.get("configmap", "")
        dep_name = target.get("deployment", "")
        sid = target.get("server_id", cm_name)
        if not cm_name or not dep_name:
            skipped.append(f"{sid}(no configmap/deployment)")
            continue
        try:
            sec_cm = core.read_namespaced_config_map(cm_name, NAMESPACE)
            if sec_cm.data is None:
                sec_cm.data = {}
            sec_conf = sec_cm.data.get("knot.conf", "")
            if not sec_conf:
                skipped.append(f"{sid}(no knot.conf)")
                continue
            new_conf = ensure_zone_in_knot_conf_secondary(
                sec_conf,
                zone_name,
                master=f"peer-{primary_id}" if primary_id else "primary-remote",
                acl=[f"peer-{primary_id}-notify"] if primary_id else "notify-allowed",
            )
            if new_conf == sec_conf:
                skipped.append(f"{sid}(already present)")
                continue
            sec_cm.data["knot.conf"] = new_conf
            core.patch_namespaced_config_map(cm_name, NAMESPACE, sec_cm)
            _trigger_knot_restart(apps, dep_name)
            synced.append(sid)
        except Exception as e:  # noqa: BLE001
            logger.warning("Sync zone %s to %s failed: %s", zone_name, sid, e)
    return ZoneSyncResult(zone=zone_name, synced_secondaries=synced, skipped=skipped)


# ---------------------------------------------------------------------------
# Ingress Wizard — вспомогательные эндпоинты для зон
# ---------------------------------------------------------------------------

class FqdnRecord(BaseModel):
    fqdn: str
    rtype: str


class ZoneFqdnsResponse(BaseModel):
    records: List[FqdnRecord]


@app.get("/api/zones/{zone_name}/fqdns", response_model=ZoneFqdnsResponse)
def get_zone_fqdns(
    zone_name: str,
    _: Dict[str, Any] = Depends(get_current_user),
) -> ZoneFqdnsResponse:
    """FQDN из зоны, пригодные для Ingress (A / AAAA / CNAME записи)."""
    validate_zone_name(zone_name)
    core, _ = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    key = f"{zone_name}.zone"
    data = cm.data or {}
    if key not in data:
        raise HTTPException(status_code=404, detail=f"Зона {zone_name} не найдена")
    try:
        form = zone_text_to_form(zone_name, data[key])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Не удалось разобрать зону: {e}") from e

    seen: dict[str, str] = {}
    for rec in form.get("records", []):
        rtype = str(rec.get("rtype") or "").upper()
        if rtype not in ("A", "AAAA", "CNAME"):
            continue
        name = str(rec.get("name") or "@").strip()
        if name == "@" or name == zone_name:
            fqdn = zone_name
        elif name.endswith("."):
            fqdn = name.rstrip(".")
        else:
            fqdn = f"{name}.{zone_name}"
        if fqdn not in seen:
            seen[fqdn] = rtype

    records = [FqdnRecord(fqdn=f, rtype=t) for f, t in sorted(seen.items())]
    return ZoneFqdnsResponse(records=records)


# ---------------------------------------------------------------------------
# Ingress Wizard — модели и эндпоинты
# ---------------------------------------------------------------------------

class NamespacesResponse(BaseModel):
    namespaces: List[str]


class ServicePortInfo(BaseModel):
    name: Optional[str] = None
    port: int
    protocol: str = "TCP"


class ServiceInfo(BaseModel):
    name: str
    ports: List[ServicePortInfo]


class ServicesResponse(BaseModel):
    services: List[ServiceInfo]


class IngressPathModel(BaseModel):
    path: str = "/"
    path_type: str = "Prefix"
    service_name: str
    service_port: int


class IngressRuleModel(BaseModel):
    host: Optional[str] = None
    paths: List[IngressPathModel] = Field(default_factory=list)


class IngressTlsModel(BaseModel):
    secret_name: str
    hosts: List[str] = Field(default_factory=list)


class IngressRenderRequest(BaseModel):
    name: str
    namespace: str
    ingress_class: Optional[str] = None
    rules: List[IngressRuleModel] = Field(default_factory=list)
    tls: Optional[IngressTlsModel] = None
    annotations: Dict[str, str] = Field(default_factory=dict)


class IngressRenderResponse(BaseModel):
    yaml: str


@app.get("/api/k8s/namespaces", response_model=NamespacesResponse)
def list_k8s_namespaces(
    _: Dict[str, Any] = Depends(get_current_user),
) -> NamespacesResponse:
    """Список пространств имён кластера."""
    try:
        core, _ = get_clients()
        ns_list = core.list_namespace()
        names = sorted(ns.metadata.name for ns in ns_list.items if ns.metadata)
        return NamespacesResponse(namespaces=names)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Не удалось получить пространства имён: {e}") from e


@app.get("/api/k8s/services", response_model=ServicesResponse)
def list_k8s_services(
    namespace: str = Query(...),
    _: Dict[str, Any] = Depends(get_current_user),
) -> ServicesResponse:
    """Список сервисов и портов в пространстве имён."""
    try:
        core, _ = get_clients()
        svc_list = core.list_namespaced_service(namespace)
        services: List[ServiceInfo] = []
        for svc in svc_list.items:
            if not svc.metadata:
                continue
            ports = []
            for p in (svc.spec.ports or []) if svc.spec else []:
                ports.append(ServicePortInfo(name=p.name, port=p.port, protocol=p.protocol or "TCP"))
            services.append(ServiceInfo(name=svc.metadata.name, ports=ports))
        services.sort(key=lambda s: s.name)
        return ServicesResponse(services=services)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Не удалось получить сервисы: {e}") from e


def _build_ingress_dict(body: IngressRenderRequest) -> dict:
    metadata: dict = {"name": body.name, "namespace": body.namespace}
    if body.annotations:
        metadata["annotations"] = dict(body.annotations)

    spec: dict = {}
    if body.ingress_class:
        spec["ingressClassName"] = body.ingress_class

    rules = []
    for rule in body.rules:
        http_paths = [
            {
                "path": p.path,
                "pathType": p.path_type,
                "backend": {
                    "service": {
                        "name": p.service_name,
                        "port": {"number": p.service_port},
                    }
                },
            }
            for p in rule.paths
        ]
        rule_obj: dict = {"http": {"paths": http_paths}}
        if rule.host:
            rule_obj["host"] = rule.host
        rules.append(rule_obj)
    if rules:
        spec["rules"] = rules

    if body.tls:
        spec["tls"] = [{"secretName": body.tls.secret_name, "hosts": list(body.tls.hosts)}]

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": metadata,
        "spec": spec,
    }


class IngressItem(BaseModel):
    name: str
    namespace: str
    ingress_class: Optional[str] = None
    hosts: List[str]
    rules: List[IngressRuleModel]
    tls: Optional[IngressTlsModel] = None
    annotations: Dict[str, str] = Field(default_factory=dict)
    age: str = ""


class IngressListResponse(BaseModel):
    ingresses: List[IngressItem]


def _format_age(ts: Any) -> str:
    if ts is None:
        return ""
    try:
        delta = dt.datetime.now(dt.timezone.utc) - ts
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}с"
        if secs < 3600:
            return f"{secs // 60}м"
        if secs < 86400:
            return f"{secs // 3600}ч"
        return f"{secs // 86400}д"
    except Exception:  # noqa: BLE001
        return ""


def _parse_k8s_ingress(ing: Any) -> IngressItem:
    meta = ing.metadata or {}
    spec = ing.spec or {}
    name = meta.name or ""
    namespace = meta.namespace or ""
    ingress_class = spec.ingress_class_name or None
    annotations = dict(meta.annotations or {})
    age = _format_age(meta.creation_timestamp)

    rules: List[IngressRuleModel] = []
    hosts: List[str] = []
    for r in spec.rules or []:
        host = r.host or None
        if host:
            hosts.append(host)
        paths: List[IngressPathModel] = []
        http = r.http
        if http:
            for p in http.paths or []:
                path_str = p.path or "/"
                path_type = p.path_type or "Prefix"
                svc_name = ""
                svc_port = 80
                if p.backend and p.backend.service:
                    svc_name = p.backend.service.name or ""
                    port_obj = p.backend.service.port
                    if port_obj:
                        svc_port = port_obj.number or 80
                paths.append(IngressPathModel(
                    path=path_str,
                    path_type=path_type,
                    service_name=svc_name,
                    service_port=svc_port,
                ))
        rules.append(IngressRuleModel(host=host, paths=paths))

    tls: Optional[IngressTlsModel] = None
    for t in spec.tls or []:
        tls = IngressTlsModel(
            secret_name=t.secret_name or "",
            hosts=list(t.hosts or []),
        )
        break  # берём первый TLS-блок

    return IngressItem(
        name=name,
        namespace=namespace,
        ingress_class=ingress_class,
        hosts=hosts,
        rules=rules,
        tls=tls,
        annotations=annotations,
        age=age,
    )


@app.get("/api/k8s/ingresses", response_model=IngressListResponse)
def list_k8s_ingresses(
    namespace: Optional[str] = Query(None),
    _: Dict[str, Any] = Depends(get_current_user),
) -> IngressListResponse:
    """Список Ingress-ресурсов кластера (все namespace или один)."""
    try:
        core, _ = get_clients()
        net = client.NetworkingV1Api()
        if namespace:
            raw = net.list_namespaced_ingress(namespace)
        else:
            raw = net.list_ingress_for_all_namespaces()
        ingresses = sorted(
            [_parse_k8s_ingress(ing) for ing in raw.items],
            key=lambda i: (i.namespace, i.name),
        )
        return IngressListResponse(ingresses=ingresses)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Не удалось получить Ingress-ресурсы: {e}") from e


@app.post("/api/k8s/ingress/render", response_model=IngressRenderResponse)
def render_ingress_manifest(
    body: IngressRenderRequest,
    _: Dict[str, Any] = Depends(get_current_user),
) -> IngressRenderResponse:
    """Сгенерировать YAML-манифест Ingress по параметрам мастера."""
    ingress_dict = _build_ingress_dict(body)
    yaml = _YAML()
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    buf = StringIO()
    yaml.dump(ingress_dict, buf)
    return IngressRenderResponse(yaml=buf.getvalue())


# ---------------------------------------------------------------------------
# cert-manager Webhook endpoints (DNS-01 challenge)
# cert-manager calls these when using webhook solver type
# Paths: /{groupName}/present, /{groupName}/cleanup, /{groupName}/healthz
# APIService paths (with version): /{groupName}/v1/present, etc.
# ---------------------------------------------------------------------------


WEBHOOK_VERSION = "v1"


@app.get(f"/{WEBHOOK_GROUP_NAME}/healthz")
@app.get(f"/{WEBHOOK_GROUP_NAME}/{WEBHOOK_VERSION}/healthz")
def webhook_healthz():
    return {"status": "ok"}


def _do_present(body: WebhookChallengeRequest):
    dns_name = body.dnsName.strip().rstrip(".")
    zone_name = body.zone.strip().rstrip(".")
    if not dns_name or not zone_name:
        raise HTTPException(status_code=400, detail="Missing dnsName or zone")

    core, _ = get_clients()
    cm_data = _read_knot_conf_map(core)
    zones = sorted(k[:-5] for k in cm_data if k.endswith(".zone"))

    if zone_name not in zones:
        raise HTTPException(status_code=404, detail=f"Zone {zone_name} not found in ConfigMap")

    txt_name = dns_name.removesuffix("." + zone_name)
    value = body.key
    _webhook_present(core, zone_name, txt_name, value)
    return {"status": "ok"}


def _do_cleanup(body: WebhookChallengeRequest):
    dns_name = body.dnsName.strip().rstrip(".")
    zone_name = body.zone.strip().rstrip(".")
    if not dns_name or not zone_name:
        raise HTTPException(status_code=400, detail="Missing dnsName or zone")

    core, _ = get_clients()
    txt_name = dns_name.removesuffix("." + zone_name)
    _webhook_cleanup(core, zone_name, txt_name)
    return {"status": "ok"}


@app.post(f"/{WEBHOOK_GROUP_NAME}/present")
@app.post(f"/{WEBHOOK_GROUP_NAME}/{WEBHOOK_VERSION}/present")
def webhook_present(body: WebhookChallengeRequest):
    return _do_present(body)


@app.post(f"/{WEBHOOK_GROUP_NAME}/cleanup")
@app.post(f"/{WEBHOOK_GROUP_NAME}/{WEBHOOK_VERSION}/cleanup")
def webhook_cleanup(body: WebhookChallengeRequest):
    return _do_cleanup(body)


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
