from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import db as _db

router = APIRouter(prefix="/api/zones", tags=["Zones"])

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)


def _format_zone(z: dict | None) -> dict | None:
    if z is None:
        return None
    z["name"] = z["zone_name"]
    z["is_follower"] = z.get("follower_of") is not None
    return z


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
    """Получить список зон из БД."""
    zones = [_format_zone(z) for z in _db.list_zones(include_deleted=include_deleted)]
    return {"zones": zones}


@router.get("/{zone_name}")
def get_zone(
    zone_name: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Получить метаданные зоны из БД."""
    zone = _format_zone(_db.get_zone(zone_name))
    if not zone:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    return {"zone": zone}


@router.delete("/{zone_name}", status_code=200)
def delete_zone(
    zone_name: str,
    hard: bool = Query(False, alias="hard"),
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    """Удалить зону из ConfigMap и БД.

    По умолчанию — мягкое удаление (deleted=1).
    ?hard=true — полное удаление из БД вместе с версиями.

    Удаляет zone-файл из ConfigMap, убирает блок из knot.conf,
    перезапускает Knot primary и secondary.
    """
    zone = _db.get_zone(zone_name)
    if not zone:
        raise HTTPException(status_code=404, detail="Зона не найдена")

    from app.main import _delete_zone_from_knot
    from app.main import get_clients
    core, apps = get_clients()
    cm_result = _delete_zone_from_knot(zone_name, core, apps)

    if hard:
        _db.hard_delete_zone(zone_name)
        return {"status": "hard_deleted", **cm_result}
    else:
        _db.soft_delete_zone(zone_name)
        return {"status": "soft_deleted", **cm_result}


@router.post("/{zone_name}/restore", status_code=200)
def restore_zone(
    zone_name: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, str]:
    """Восстановить мягко удалённую зону (deleted=0)."""
    ok = _db.restore_zone(zone_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    return {"status": "restored"}