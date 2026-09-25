from __future__ import annotations

import base64
import datetime as dt
import os
from typing import Any, Dict

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pydantic import BaseModel, Field

from app import crypto_utils, db as _db

router = APIRouter(prefix="/api/tsig-keys")

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
def list_keys(_: Dict[str, Any] = Depends(_get_current_user)) -> Dict[str, Any]:
    conn = _db.get_db()
    rows = conn.execute(
        "SELECT key_id, algorithm, scope, status, description, created_at, revoked_at "
        "FROM tsig_keys ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return {"keys": [dict(r) for r in rows]}


class CreateKeyBody(BaseModel):
    name: str = ""
    scope: str = "dns_update"
    algorithm: str = Field(default="hmac-sha256", description="hmac-sha256, hmac-sha1, hmac-sha512")


@router.post("", status_code=201)
def create_key(
    body: CreateKeyBody,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, Any]:
    kid = body.name.strip()
    if not kid:
        raise HTTPException(status_code=400, detail="name обязателен")
    if body.scope not in ("axfr", "dns_update"):
        raise HTTPException(status_code=400, detail="scope: axfr или dns_update")

    raw_secret = os.urandom(32)
    secret_b64 = base64.b64encode(raw_secret).decode()
    encrypted = crypto_utils.encrypt_secret(secret_b64)

    conn = _db.get_db()
    existing = conn.execute("SELECT key_id FROM tsig_keys WHERE key_id = ?", (kid,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=409, detail=f"Ключ '{kid}' уже существует")
    try:
        conn.execute(
            "INSERT INTO tsig_keys (key_id, algorithm, secret_encrypted, scope) VALUES (?, ?, ?, ?)",
            (kid, body.algorithm, encrypted, body.scope),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "key_id": kid,
        "algorithm": body.algorithm,
        "scope": body.scope,
        "secret": secret_b64,
        "status": "active",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }


@router.post("/{key_id}/revoke")
def revoke_key(
    key_id: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, str]:
    conn = _db.get_db()
    row = conn.execute(
        "SELECT status FROM tsig_keys WHERE key_id = ?", (key_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Ключ не найден")
    if row["status"] == "revoked":
        conn.close()
        return {"status": "already_revoked"}
    conn.execute(
        "UPDATE tsig_keys SET status = 'revoked', revoked_at = datetime('now') WHERE key_id = ?",
        (key_id,),
    )
    conn.commit()
    conn.close()
    return {"status": "revoked"}


@router.delete("/{key_id}")
def delete_key(
    key_id: str,
    _: Dict[str, Any] = Depends(_get_current_user),
) -> Dict[str, str]:
    conn = _db.get_db()
    row = conn.execute(
        "SELECT key_id FROM tsig_keys WHERE key_id = ?", (key_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Ключ не найден")
    conn.execute("DELETE FROM tsig_keys WHERE key_id = ?", (key_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}