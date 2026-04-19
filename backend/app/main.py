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
from pydantic import BaseModel

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
        f"      - domain: {zone_name}\n"
        f"        file: /zones/{zone_name}.zone\n"
        f"        acl: [axfr-allowed]\n"
        f"        dnssec-signing: off\n"
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


@app.put("/api/zones/{zone_name}")
def update_zone(
    zone_name: str,
    body: ZoneBody,
    _: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, str]:
    validate_zone_name(zone_name)
    core, apps = get_clients()
    cm = core.read_namespaced_config_map(CONFIGMAP_NAME, NAMESPACE)
    if cm.data is None:
        cm.data = {}
    key = f"{zone_name}.zone"
    is_new = key not in cm.data
    cm.data[key] = body.content
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
