"""Tests for worker/main.py::run_daily_workout_push -- the date-window/push_status filtering and
rate-limit-abort behavior. run_daily_sync/run_daily_backup have no direct unit tests in this
codebase (their own building blocks -- sync_garmin_connect, create_backup -- are tested
directly); run_daily_workout_push gets one here because its window/filter logic is new and
worth guarding independent of push_planned_workout's own tests (tests/test_planned_workouts.py).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sqlalchemy import Engine, select

from perseverer.config import Settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, metadata, planned_workout
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.worker.main import run_daily_workout_push


def _engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    return engine


def _insert(engine: Engine, *, local_date: str, push_status: str = "draft") -> int:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        result = conn.execute(
            planned_workout.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date=local_date,
                sport="running",
                name="Test",
                source_text="Warmup 10m",
                estimated_duration_s=600,
                push_status=push_status,
                created_at=now,
                updated_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        workout_id = result.inserted_primary_key[0]
        conn.commit()
    assert isinstance(workout_id, int)
    return workout_id


def test_only_pushes_workouts_due_within_the_window_and_not_already_pushed(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    today = dt.datetime.now(dt.UTC).date()
    in_window = _insert(engine, local_date=(today + dt.timedelta(days=3)).isoformat())
    already_pushed = _insert(
        engine, local_date=(today + dt.timedelta(days=1)).isoformat(), push_status="pushed"
    )
    too_far_out = _insert(engine, local_date=(today + dt.timedelta(days=30)).isoformat())
    in_the_past = _insert(engine, local_date=(today - dt.timedelta(days=1)).isoformat())

    calls: list[int] = []

    def fake_push(conn: Any, *, athlete_id: str, planned_workout_id: int, **kwargs: Any) -> Any:
        calls.append(planned_workout_id)
        from perseverer.planned_workouts import PushResult

        return PushResult(success=True, garmin_workout_id=1, error=None)

    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.push_planned_workout", side_effect=fake_push),
    ):
        run_daily_workout_push()

    assert calls == [in_window]
    assert already_pushed not in calls
    assert too_far_out not in calls
    assert in_the_past not in calls


def test_rate_limit_abort_stops_the_loop_without_marking_remaining_failed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    today = dt.datetime.now(dt.UTC).date()
    first = _insert(engine, local_date=today.isoformat())
    second = _insert(engine, local_date=(today + dt.timedelta(days=1)).isoformat())

    from perseverer.adapters.garmin_connect import GarminRateLimitAborted

    def fake_push(conn: Any, *, athlete_id: str, planned_workout_id: int, **kwargs: Any) -> Any:
        raise GarminRateLimitAborted("429")

    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.push_planned_workout", side_effect=fake_push),
    ):
        run_daily_workout_push()

    with engine.connect() as conn:
        rows = {
            r.id: r.push_status
            for r in conn.execute(select(planned_workout.c.id, planned_workout.c.push_status))
        }
    # Neither row was ever marked push_failed -- a rate-limit abort isn't this workout's fault
    # (push_planned_workout deliberately lets it propagate rather than writing push_status, see
    # its own docstring), and the loop's `break` means the run stops here entirely.
    assert rows[first] == "draft"
    assert rows[second] == "draft"


def test_no_due_workouts_is_a_clean_noop(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    settings = Settings(data_dir=tmp_path)
    with (
        patch("perseverer.worker.main.get_settings", return_value=settings),
        patch("perseverer.worker.main.make_engine", return_value=engine),
        patch("perseverer.worker.main.push_planned_workout") as mock_push,
    ):
        run_daily_workout_push()
    mock_push.assert_not_called()
