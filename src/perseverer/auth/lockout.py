"""Login brute-force lockout (Phase 9 hardening, ADR 0014). `/auth/login` previously had no
attempt-count limit at all -- constant-time password comparison (see passwords.py) protects
against *timing* attacks, but nothing stopped an unlimited number of guesses.

DB-backed, not in-memory: `api.Dockerfile` runs uvicorn with 2 workers, which don't share
process memory -- an in-memory counter would let an attacker halve their effective lockout by
spreading requests across both. Every worker reads/writes the same SQLite file, so this is the
one shared state they actually have in common.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, delete, func, select

from perseverer.db.schema import auth_login_attempt

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW = timedelta(minutes=15)
# How long a username's attempt history is kept before being pruned -- generous relative to
# LOCKOUT_WINDOW so it never interferes with a lockout decision, just keeps the table from
# growing unbounded on a login endpoint that (by this app's own single-athlete nature) never
# sees real volume anyway.
_RETENTION = timedelta(hours=24)


def is_locked_out(conn: Connection, username: str, *, now: datetime | None = None) -> bool:
    """True when `username` has had >= MAX_FAILED_ATTEMPTS failed logins within LOCKOUT_WINDOW.
    Deliberately keyed on username, not source IP -- this app sits behind a reverse proxy and
    only trusts X-Forwarded-For from that proxy's own known IP (see api/dependencies.py), so an
    IP-based counter would either trust an unverifiable client-supplied header or bucket every
    request behind the same proxy IP together; username is the one identity actually known here.
    """
    now = now or datetime.now(UTC)
    count = conn.execute(
        select(func.count())
        .select_from(auth_login_attempt)
        .where(
            auth_login_attempt.c.username == username,
            auth_login_attempt.c.success.is_(False),
            auth_login_attempt.c.attempted_at >= (now - LOCKOUT_WINDOW).replace(tzinfo=None),
        )
    ).scalar_one()
    return count >= MAX_FAILED_ATTEMPTS


def record_attempt(
    conn: Connection, username: str, *, success: bool, now: datetime | None = None
) -> None:
    """Records one login attempt and opportunistically prunes this username's history older
    than _RETENTION -- called on every /auth/login request, successful or not, so pruning stays
    naturally amortized rather than needing its own separate scheduled job."""
    now = now or datetime.now(UTC)
    naive_now = now.replace(tzinfo=None)
    conn.execute(
        auth_login_attempt.insert().values(
            username=username, attempted_at=naive_now, success=success
        )
    )
    conn.execute(
        delete(auth_login_attempt).where(
            auth_login_attempt.c.username == username,
            auth_login_attempt.c.attempted_at < naive_now - _RETENTION,
        )
    )
