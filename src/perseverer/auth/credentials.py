"""The one username/password check, shared by `POST /auth/login` (JWT session) and the MCP OAuth
login page (`api/routers/oauth.py`) so the lockout and constant-time discipline has a single
implementation to audit, not two. See auth/lockout.py and docs/adr/0008-phase-5-frontend.md.
"""

from __future__ import annotations

from sqlalchemy import Connection, select

from perseverer.auth.lockout import is_locked_out, record_attempt
from perseverer.auth.passwords import hash_password, verify_password
from perseverer.db.schema import athlete

# Constant-time comparison target for unknown usernames -- never valid for any real password
# since verify_password would need the exact matching random salt to succeed.
_DUMMY_HASH = hash_password("")


def authenticate_athlete(conn: Connection, username: str, password: str) -> str | None:
    """Returns the athlete's id for valid credentials, else None. Records the attempt and
    commits. A locked-out username and a wrong password are indistinguishable to the caller."""
    # Computed unconditionally, before the credential check below, so a locked-out request and
    # a wrong-password request do the exact same amount of work (see auth/lockout.py's own
    # docstring) -- a lockout check must not reopen the unknown-username timing side-channel.
    locked_out = is_locked_out(conn, username)

    row = conn.execute(
        select(athlete.c.id, athlete.c.password_hash).where(athlete.c.username == username)
    ).one_or_none()
    # Always run verify_password, even for an unknown username -- against a dummy hash -- so a
    # nonexistent username doesn't return measurably faster than a wrong password.
    stored_hash = row.password_hash if row is not None and row.password_hash else _DUMMY_HASH
    password_ok = verify_password(password, stored_hash)
    valid = row is not None and row.password_hash is not None and password_ok

    record_attempt(conn, username, success=valid and not locked_out)
    conn.commit()

    if locked_out or not valid:
        return None
    assert row is not None
    return str(row.id)
