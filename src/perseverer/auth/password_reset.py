"""Forgotten-password recovery: the reset link emailed by `POST /auth/forgot-password` and checked
by `POST /auth/reset-password`. See docs/ARCHITECTURE.md.

The link carries a signed JWT (HS256, `Settings.jwt_secret`) rather than a stored token:
- `purpose` keeps it from ever being accepted as a session token, and a session token from being
  accepted here;
- `pwh`, a fingerprint of the athlete's password hash at issue time, makes it single-use: setting
  the new password changes the hash, which invalidates this link and every older one;
- `exp` limits it to RESET_TOKEN_LIFETIME.

Requests are throttled per account through the login-attempt table (auth/lockout.py), under the
key `password-reset:<athlete id>`, so the form can't be used to flood an inbox.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html as html_lib

import jwt
from sqlalchemy import Connection, func, or_, select

from perseverer.auth.lockout import is_locked_out, record_attempt
from perseverer.db.schema import athlete

_ALGORITHM = "HS256"
_PURPOSE = "password_reset"
RESET_TOKEN_LIFETIME = dt.timedelta(hours=1)


class InvalidResetToken(Exception):
    pass


def _fingerprint(password_hash: str | None) -> str:
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


def create_reset_token(athlete_id: str, password_hash: str | None, secret: str) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": athlete_id,
        "purpose": _PURPOSE,
        "pwh": _fingerprint(password_hash),
        "iat": now,
        "exp": now + RESET_TOKEN_LIFETIME,
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def verify_reset_token(conn: Connection, token: str, secret: str) -> str:
    """The athlete id a valid, unused, unexpired reset token was issued for; raises
    `InvalidResetToken` otherwise."""
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise InvalidResetToken(str(exc)) from exc
    athlete_id = payload.get("sub")
    if payload.get("purpose") != _PURPOSE or not isinstance(athlete_id, str):
        raise InvalidResetToken("not a password reset token")
    row = conn.execute(
        select(athlete.c.password_hash).where(athlete.c.id == athlete_id)
    ).one_or_none()
    if row is None or payload.get("pwh") != _fingerprint(row.password_hash):
        raise InvalidResetToken("token already used or superseded")
    return athlete_id


def find_recoverable_athlete(
    conn: Connection, identifier: str
) -> tuple[str, str, str | None] | None:
    """(athlete id, email, password hash) for the account whose username or email matches
    `identifier` (case-insensitive) and that has an email to send to; None otherwise."""
    value = identifier.strip().lower()
    if not value:
        return None
    row = conn.execute(
        select(athlete.c.id, athlete.c.email, athlete.c.password_hash).where(
            or_(func.lower(athlete.c.username) == value, func.lower(athlete.c.email) == value),
            athlete.c.username.is_not(None),
            athlete.c.email.is_not(None),
        )
    ).first()
    if row is None or not row.email:
        return None
    return str(row.id), str(row.email), row.password_hash


def allow_reset_request(conn: Connection, athlete_id: str) -> bool:
    """Records one reset request for this account; False once it has asked too often recently
    (the same limits as failed logins: MAX_FAILED_ATTEMPTS within LOCKOUT_WINDOW)."""
    key = f"password-reset:{athlete_id}"
    allowed = not is_locked_out(conn, key)
    record_attempt(conn, key, success=False)
    return allowed


def reset_email(link: str) -> tuple[str, str, str]:
    """(subject, html, text) of the reset email. Inline styles only, like the summary emails."""
    minutes = int(RESET_TOKEN_LIFETIME.total_seconds() // 60)
    subject = "Reset your Perseverer password"
    text = (
        "Someone asked to reset the password of your Perseverer account.\n\n"
        f"Choose a new password here (the link works once, for {minutes} minutes):\n{link}\n\n"
        "If it wasn't you, ignore this email: your password stays the same.\n"
    )
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#1f2430;'
        'max-width:520px">'
        '<p style="margin:0 0 16px">Someone asked to reset the password of your Perseverer '
        "account.</p>"
        f'<p style="margin:0 0 16px"><a href="{html_lib.escape(link)}" style="display:inline-block;'
        "background:#2563eb;color:#ffffff;text-decoration:none;padding:10px 18px;"
        'border-radius:6px;font-weight:bold">Choose a new password</a></p>'
        f'<p style="margin:0 0 16px;color:#5b6475">The link works once, for {minutes} '
        "minutes. If it wasn't you, ignore this email: your password stays the same.</p>"
        "</div>"
    )
    return subject, html, text
