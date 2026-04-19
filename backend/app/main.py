"""dnsadmin: FastAPI + статический SPA + JWT. Управление knot-config и restart Knot."""

from __future__ import annotations

import base64
import datetime as dt
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from kubernetes import client, config
from pydantic import BaseModel, Field

from app.dns_probe import knot_probe
from app.dnssec_ds import fetch_ds_records_for_zone
from app.knot_conf import list_zone_dnssec_flags, set_zone_dnssec_signing, zone_declared_in_knot_conf
from app.knot_editor_model import KnotEditorModel, apply_editor_model, extract_editor_model
from app.knot_validate import run_knotc_conf_check
from app.knot_yaml import load_schema, parse_knot_conf, serialize_knot_conf
from app.zone_editor import form_to_zone_text, validate_zonefile, zone_text_to_form

logger = logging.getLogger(__name__)

NAMESPACE = os.environ.get("NAMESPACE", "dns-knot")
CONFIGMAP_NAME = os.environ.get("KNOT_CONFIGMAP_NAME", "knot-config")
KNOT_DEPLOYMENT = os.environ.get("KNOT_DEPLOYMENT_NAME", "knot")
DEFAULT_ZONE = os.environ.get("DEFAULT_ZONE", "k3s.local")
KNOT_AXFR_SECRET_NAME = os.environ.get("KNOT_AXFR_SECRET_NAME", "knot-axfr")
KNOT_AXFR_SECRET_KEY = os.environ.get("KNOT_AXFR_SECRET_KEY", "axfr.conf")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

STATIC_DIR = Path(os.environ.get("STATIC_DIR", "")).resolve() if os.environ.get("STATIC_DIR") else (
    Path(__file__).resolve().parent.parent.parent / "dist"
).resolve()

bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(title="dnsadmin", version="2.1.0", docs_url=None, redoc_url=None)


def get_clients():
    config.load_incluster_config()
    return client.CoreV1Api(), client.AppsV1Api()


def _read_knot_conf_map(core: client.CoreV1Api) -> Dict[str, str]:
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    return dict(cm.data or {})


def _zone_files_from_cm(data: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in data.items() if k.endswith(".zone")}


def _read_axfr_secret(core: client.CoreV1Api) -> str | None:
    try:
        sec = core.read_namespaced_secret(KNOT_AXFR_SECRET_NAME, NAMESPACE)
    except Exception:  # noqa: BLE001 — Secret может отсутствовать или RBAC
        return None
    raw = (sec.data or {}).get(KNOT_AXFR_SECRET_KEY)
    if not raw:
        return None
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


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
        }
    core, _ = get_clients()
    if axfr_override is not None:
        axfr = axfr_override
    else:
        axfr = _read_axfr_secret(core)
    res = run_knotc_conf_check(knot_conf, _zone_files_from_cm(cm_data), axfr_yaml=axfr)
    return {
        "ok": res.ok,
        "yaml_ok": True,
        "yaml_error": None,
        "knotc": {"ran": res.ran, "ok": res.ok, "message": res.message},
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


class AxfrConfBody(BaseModel):
    content: str


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
    IP pod Knot (при hostNetwork ≈ адрес listen в knot.conf).
    ClusterIP сервиса knot при bind только на IP ноды не подходит.
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


def _knot_dns_reachable_host() -> str:
    """
    Куда слать DNS-запросы (SOA/DS): явный KNOT_DNS_PROBE_HOST, иначе IP pod Knot, иначе KNOT_DNS_HOST.
    """
    h = os.environ.get("KNOT_DNS_PROBE_HOST", "").strip()
    if h:
        return h
    ip = _knot_running_pod_ip()
    if ip:
        return ip
    return os.environ.get("KNOT_DNS_HOST", "knot")


@app.get("/api/dns-health")
def dns_health(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """SOA (UDP). Цель: KNOT_DNS_PROBE_HOST → IP pod knot → KNOT_DNS_HOST."""
    ok, msg, ms = knot_probe(_knot_dns_reachable_host())
    return {"ok": ok, "message": msg, "latency_ms": round(ms, 2) if ms is not None else None}


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


@app.get("/api/knot-conf/axfr")
def get_axfr_fragment(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, str]:
    core, _ = get_clients()
    content = _read_axfr_secret(core)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail=f"Secret {KNOT_AXFR_SECRET_NAME!r} недоступен или нет ключа {KNOT_AXFR_SECRET_KEY!r}",
        )
    return {"content": content}


@app.put("/api/knot-conf/axfr")
def put_axfr_fragment(
    body: AxfrConfBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    core, apps = get_clients()
    data = _read_knot_conf_map(core)
    knot_conf = data.get("knot.conf") or ""
    try:
        parse_knot_conf(body.content)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"YAML axfr не разобран: {e}") from e
    res = run_knotc_conf_check(
        knot_conf,
        _zone_files_from_cm(data),
        axfr_yaml=body.content,
    )
    if not res.ok:
        raise HTTPException(
            status_code=400,
            detail={"knotc": {"ran": res.ran, "ok": res.ok, "message": res.message}},
        )
    sec = core.read_namespaced_secret(KNOT_AXFR_SECRET_NAME, NAMESPACE)
    if sec.data is None:
        sec.data = {}
    sec.data[KNOT_AXFR_SECRET_KEY] = base64.b64encode(body.content.encode("utf-8")).decode("ascii")
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
        ds_list, msg = fetch_ds_records_for_zone(host, zone_name, port=port)
    except OSError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not ds_list:
        raise HTTPException(status_code=404, detail=msg)
    return {"ds": ds_list, "message": msg}


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
    cm.data[key] = content
    if is_new and cm.data.get("knot.conf"):
        cm.data["knot.conf"] = ensure_zone_in_knot_conf(cm.data["knot.conf"], zone_name)
    core.patch_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE, cm)

    ts = _trigger_knot_restart(apps)
    return {"status": "ok", "restarted_at": ts}


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
