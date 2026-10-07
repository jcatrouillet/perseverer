"""JWT session tokens for athlete login. HS256, signed with
Settings.jwt_secret -- unset secret means the login endpoint fails closed (503), mirroring the
existing api_key unset-503 precedent in api/dependencies.py::require_api_key. See
docs/ARCHITECTURE.md.

Verified against the installed pyjwt 2.13.0 API directly (jwt.encode/decode signatures,
ExpiredSignatureError/InvalidTokenError) rather than assumed from memory, per this project's
standing rule for vendor libraries.
"""

from __future__ import annotations

import datetime as dt

import jwt

_ALGORITHM = "HS256"


class InvalidSessionToken(Exception):
    pass


def create_session_token(athlete_id: str, secret: str, expiry_days: int) -> tuple[str, dt.datetime]:
    """A signed JWT for `POST /auth/login`; returns it and its expiry."""
    now = dt.datetime.now(dt.UTC)
    expires_at = now + dt.timedelta(days=expiry_days)
    payload = {"sub": athlete_id, "iat": now, "exp": expires_at}
    token = jwt.encode(payload, secret, algorithm=_ALGORITHM)
    return token, expires_at


def verify_session_token(token: str, secret: str) -> str:
    """The athlete id (`sub`) of a valid session token; raises `InvalidSessionToken` otherwise."""
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise InvalidSessionToken(str(exc)) from exc
    athlete_id = payload.get("sub")
    if not isinstance(athlete_id, str):
        raise InvalidSessionToken("token missing 'sub' claim")
    return athlete_id
