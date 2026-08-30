"""auth/lockout.py unit tests -- window-boundary and pruning behavior not exercised by the
API-level lockout tests in tests/api/test_login.py (which cover the actual /auth/login
integration, not these edge cases directly)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, select

from perseverer.auth.lockout import (
    LOCKOUT_WINDOW,
    MAX_FAILED_ATTEMPTS,
    is_locked_out,
    record_attempt,
)
from perseverer.db.engine import make_engine
from perseverer.db.schema import auth_login_attempt, metadata

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    return engine


def test_not_locked_out_with_no_attempts(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        assert not is_locked_out(conn, "jerome", now=NOW)


def test_locked_out_at_exactly_max_attempts(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        for _ in range(MAX_FAILED_ATTEMPTS):
            record_attempt(conn, "jerome", success=False, now=NOW)
        conn.commit()
        assert is_locked_out(conn, "jerome", now=NOW)


def test_not_locked_out_one_below_max_attempts(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            record_attempt(conn, "jerome", success=False, now=NOW)
        conn.commit()
        assert not is_locked_out(conn, "jerome", now=NOW)


def test_successful_attempts_never_count_toward_lockout(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        for _ in range(MAX_FAILED_ATTEMPTS + 5):
            record_attempt(conn, "jerome", success=True, now=NOW)
        conn.commit()
        assert not is_locked_out(conn, "jerome", now=NOW)


def test_failures_outside_the_window_do_not_count(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    old = NOW - LOCKOUT_WINDOW - timedelta(minutes=1)
    with engine.connect() as conn:
        for _ in range(MAX_FAILED_ATTEMPTS):
            record_attempt(conn, "jerome", success=False, now=old)
        conn.commit()
        assert not is_locked_out(conn, "jerome", now=NOW)


def test_record_attempt_prunes_history_older_than_retention(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    ancient = NOW - timedelta(hours=48)
    with engine.connect() as conn:
        record_attempt(conn, "jerome", success=False, now=ancient)
        conn.commit()
        rows_before = conn.execute(
            select(auth_login_attempt).where(auth_login_attempt.c.username == "jerome")
        ).fetchall()
        assert len(rows_before) == 1

        # A later attempt should prune the ancient row during its own insert.
        record_attempt(conn, "jerome", success=False, now=NOW)
        conn.commit()
        rows_after = conn.execute(
            select(auth_login_attempt).where(auth_login_attempt.c.username == "jerome")
        ).fetchall()
        assert len(rows_after) == 1
        assert rows_after[0].success is False
