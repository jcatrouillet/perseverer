"""POST /auth/login -- issues a JWT session token for an athlete's username/password. Not
gated by require_api_key (same unauthenticated-route pattern as /healthz): this endpoint IS the
credential check. See docs/adr/0008-phase-5-frontend.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, select

from sporthealth.api.dependencies import SettingsDep, get_conn
from sporthealth.api.schemas.auth import LoginRequest, LoginResponse
from sporthealth.auth.passwords import hash_password, verify_password
from sporthealth.auth.tokens import create_session_token
from sporthealth.db.schema import athlete

router = APIRouter()

# Constant-time comparison target for unknown usernames -- never valid for any real password
# since verify_password would need the exact matching random salt to succeed.
_DUMMY_HASH = hash_password("")


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
) -> LoginResponse:
    if not settings.jwt_secret:
        raise HTTPException(status_code=503, detail="JWT signing not configured")

    row = conn.execute(
        select(athlete.c.id, athlete.c.password_hash).where(athlete.c.username == payload.username)
    ).one_or_none()
    # Always run verify_password, even for an unknown username -- against a dummy hash --
    # so a nonexistent username doesn't return measurably faster than a wrong password
    # (this frontend is reachable beyond a trusted network by design, see ADR 0008).
    stored_hash = row.password_hash if row is not None and row.password_hash else _DUMMY_HASH
    password_ok = verify_password(payload.password, stored_hash)
    if row is None or row.password_hash is None or not password_ok:
        raise HTTPException(status_code=401, detail="invalid username or password")

    token, expires_at = create_session_token(row.id, settings.jwt_secret, settings.jwt_expiry_days)
    return LoginResponse(access_token=token, expires_at=expires_at)
