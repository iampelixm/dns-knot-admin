"""Связь зон с DNS-серверами (zone_servers)."""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app import db as _db

router = APIRouter(prefix="/api/zones", tags=["Zone Servers"])

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)


def _get_current_user(
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
    import jwt
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


class AssignServerBody(BaseModel):
    server_id: str
    role: str = "secondary"
    tsig_key_id: str | None = None
    serial: int = 0
    settings: str = "{}"


@router.get("/{zone_name}/servers")
def list_zone_servers(
    zone_name: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Список серверов, привязанных к зоне."""
    zone = _db.get_zone(zone_name)
    if not zone:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    servers = _db.list_zone_servers(zone_name=zone_name)
    return {"servers": servers}


@router.post("/{zone_name}/servers", status_code=201)
def assign_server(
    zone_name: str,
    body: AssignServerBody,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Привязать DNS-сервер к зоне (secondary / master)."""
    zone = _db.get_zone(zone_name)
    if not zone:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    server = _db.get_dns_server(body.server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Сервер не найден")
    if body.tsig_key_id:
        conn = _db.get_db()
        row = conn.execute(
            "SELECT key_id FROM tsig_keys WHERE key_id = ?", (body.tsig_key_id,)
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=400, detail=f"TSIG-ключ '{body.tsig_key_id}' не найден")
    result = _db.assign_zone_to_server(
        zone_name=zone_name,
        server_id=body.server_id,
        role=body.role,
        tsig_key_id=body.tsig_key_id,
        serial=body.serial,
        settings=body.settings,
    )
    if not result:
        raise HTTPException(status_code=409, detail="Связь уже существует")
    return {"server": result}


@router.delete("/{zone_name}/servers/{server_id}")
def remove_server_from_zone(
    zone_name: str,
    server_id: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, str]:
    """Отвязать DNS-сервер от зоны."""
    ok = _db.remove_zone_from_server(zone_name, server_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Связь не найдена")
    return {"status": "removed"}