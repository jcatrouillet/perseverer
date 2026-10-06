"""POST /auth/login -- issues a JWT session token for an athlete's username/password. Not
gated by require_api_key (same unauthenticated-route pattern as /healthz): this endpoint IS the
credential check. See docs/adr/0008-phase-5-frontend.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection

from perseverer.api.dependencies import SettingsDep, get_conn
from perseverer.api.schemas.auth import LoginRequest, LoginResponse
from perseverer.auth.credentials import authenticate_athlete
from perseverer.auth.tokens import create_session_token

router = APIRouter()


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
) -> LoginResponse:
    if not settings.jwt_secret:
        raise HTTPException(status_code=503, detail="JWT signing not configured")

    athlete_id = authenticate_athlete(conn, payload.username, payload.password)
    # Locked out and wrong-credentials both raise the exact same 401 with the exact same
    # message -- never tell an external caller which one actually happened.
    if athlete_id is None:
        raise HTTPException(status_code=401, detail="invalid username or password")

    token, expires_at = create_session_token(
        athlete_id, settings.jwt_secret, settings.jwt_expiry_days
    )
    return LoginResponse(access_token=token, expires_at=expires_at)
