"""dnsadmin: FastAPI + статический SPA + JWT. Управление knot-config и restart Knot."""

from __future__ import annotations

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

from app.dns_probe import knot_probe_from_env
from app.zone_editor import form_to_zone_text, validate_zonefile, zone_text_to_form

logger = logging.getLogger(__name__)

NAMESPACE = os.environ.get("NAMESPACE", "dns-knot")
CONFIGMAP_NAME = os.environ.get("KNOT_CONFIGMAP_NAME", "knot-config")
KNOT_DEPLOYMENT = os.environ.get("KNOT_DEPLOYMENT_NAME", "knot")
DEFAULT_ZONE = os.environ.get("DEFAULT_ZONE", "k3s.local")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

STATIC_DIR = Path(os.environ.get("STATIC_DIR", "")).resolve() if os.environ.get("STATIC_DIR") else (
    Path(__file__).resolve().parent.parent.parent / "dist"
).resolve()

bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(title="dnsadmin", version="2.0.0", docs_url=None, redoc_url=None)


def get_clients():
    config.load_incluster_config()
    return client.CoreV1Api(), client.AppsV1Api()


class ZoneBody(BaseModel):
    content: str


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


@app.get("/api/dns-health")
def dns_health(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Проверка: Knot отвечает на SOA (UDP). Хост: KNOT_DNS_HOST, зона: DNS_HEALTH_PROBE_ZONE."""
    ok, msg, ms = knot_probe_from_env()
    return {"ok": ok, "message": msg, "latency_ms": round(ms, 2) if ms is not None else None}


@app.post("/api/auth/login", response_model=TokenResponse)
def login(body: LoginBody) -> TokenResponse:
    if body.username != ADMIN_USERNAME or body.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    return TokenResponse(access_token=_issue_token(body.username))


@app.get("/api/zones")
def list_zones(_: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, list]:
    core, _ = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    raw = cm.data or {}
    keys = sorted([k[:-5] for k in raw.keys() if k.endswith(".zone")])
    if DEFAULT_ZONE in keys:
        keys.remove(DEFAULT_ZONE)
        keys.insert(0, DEFAULT_ZONE)
    return {"zones": keys}


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

    ts = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "dnsadmin/restarted-at": ts
                    }
                }
            }
        }
    }
    apps.patch_namespaced_deployment(KNOT_DEPLOYMENT, NAMESPACE, patch)
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
