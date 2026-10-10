"""POST /auth/login -- issues a JWT session token for an athlete's username/password -- and the
forgotten-password pair, POST /auth/forgot-password and POST /auth/reset-password. None is gated
by require_api_key (same unauthenticated-route pattern as /healthz): these endpoints ARE the
credential check. docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import Connection, select

from perseverer.api.dependencies import SettingsDep, get_conn
from perseverer.api.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
)
from perseverer.auth.credentials import authenticate_athlete
from perseverer.auth.lockout import clear_failed_attempts
from perseverer.auth.password_reset import (
    InvalidResetToken,
    allow_reset_request,
    create_reset_token,
    find_recoverable_athlete,
    reset_email,
    verify_reset_token,
)
from perseverer.auth.passwords import hash_password
from perseverer.auth.tokens import create_session_token
from perseverer.config import Settings
from perseverer.db.schema import athlete
from perseverer.email_delivery import send_email

logger = logging.getLogger(__name__)

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


def _send_reset_email(settings: Settings, to: str, link: str) -> None:
    subject, html_body, text_body = reset_email(link)
    try:
        send_email(settings, to=to, subject=subject, html_body=html_body, text_body=text_body)
    except Exception:
        logger.exception("could not send the password reset email")


@router.post("/auth/forgot-password")
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background: BackgroundTasks,
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
) -> ForgotPasswordResponse:
    """Emails a one-time reset link to the account whose username or email matches, if it has an
    email address and this server can send mail. The response never says whether an account
    matched, and the email goes out after the response, so timing doesn't tell either."""
    email_configured = settings.smtp_configured and bool(settings.jwt_secret)
    if not email_configured:
        return ForgotPasswordResponse(email_configured=False)
    match = find_recoverable_athlete(conn, payload.identifier)
    if match is not None:
        athlete_id, email, password_hash = match
        allowed = allow_reset_request(conn, athlete_id)
        conn.commit()
        if allowed:
            assert settings.jwt_secret
            token = create_reset_token(athlete_id, password_hash, settings.jwt_secret)
            base = (settings.public_base_url or str(request.base_url)).rstrip("/")
            background.add_task(
                _send_reset_email, settings, email, f"{base}/reset-password?token={token}"
            )
    return ForgotPasswordResponse(email_configured=True)


@router.post("/auth/reset-password")
def reset_password(
    payload: ResetPasswordRequest,
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
) -> ResetPasswordResponse:
    """Sets a new password from a reset link. The link stops working once used (the new password
    changes the hash it is tied to); failed logins for the account are forgotten so the new
    password works at once."""
    if not settings.jwt_secret:
        raise HTTPException(status_code=503, detail="JWT signing not configured")
    try:
        athlete_id = verify_reset_token(conn, payload.token, settings.jwt_secret)
    except InvalidResetToken as e:
        raise HTTPException(
            status_code=400, detail="this reset link is invalid, expired or already used"
        ) from e
    conn.execute(
        athlete.update()
        .where(athlete.c.id == athlete_id)
        .values(password_hash=hash_password(payload.new_password))
    )
    username = conn.execute(
        select(athlete.c.username).where(athlete.c.id == athlete_id)
    ).scalar_one_or_none()
    if username:
        clear_failed_attempts(conn, username)
    conn.commit()
    return ResetPasswordResponse(success=True)
