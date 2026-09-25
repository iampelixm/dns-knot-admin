from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import db as _db

router = APIRouter(prefix="/api/zones")

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


@router.get("")
def list_zones(
    include_deleted: bool = Query(False, alias="include_deleted"),
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    zones = _db.list_zones(include_deleted=include_deleted)
    return {"zones": zones}


@router.get("/{zone_name}")
def get_zone(
    zone_name: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    zone = _db.get_zone(zone_name)
    if not zone:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    return {"zone": zone}


@router.delete("/{zone_name}", status_code=200)
def delete_zone(
    zone_name: str,
    hard: bool = Query(False, alias="hard"),
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, str]:
    zone = _db.get_zone(zone_name)
    if not zone:
        raise HTTPException(status_code=404, detail="Зона не найдена")

    if hard:
        ok = _db.hard_delete_zone(zone_name)
        return {"status": "hard_deleted"}
    else:
        _db.soft_delete_zone(zone_name)
        return {"status": "soft_deleted"}


@router.post("/{zone_name}/restore", status_code=200)
def restore_zone(
    zone_name: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, str]:
    ok = _db.restore_zone(zone_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    return {"status": "restored"}