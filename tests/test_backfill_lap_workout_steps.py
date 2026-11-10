"""Tests for backfill_lap_workout_steps.py -- fills lap.wkt_step_index and lap.lap_trigger from the
archived FIT bytes. `parse_fit` is patched (it is covered by tests/fit/test_parser.py); these
tests cover the module's own job: matching raw objects to activities, the update, and idempotency.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from perseverer.backfill_lap_workout_steps import backfill_lap_workout_steps
from perseverer.db.schema import lap
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.parser import _lap_trigger
from perseverer.fit.types import CanonicalActivity, CanonicalBatch, ParsedLap
from tests.test_backfill_lap_moving_duration import _engine, _seed_activity_with_laps

START = dt.datetime(2026, 7, 1, 2, 0, 28)


def _batch(*, step: int | None, trigger: str | None) -> CanonicalBatch:
    laps = [
        ParsedLap(
            lap_index=0,
            start_time_utc=START,
            duration_s=1008.193,
            moving_duration_s=90.0,
            distance_m=234.04,
            avg_hr=138.0,
            max_hr=150.0,
            avg_speed_mps=0.23,
            wkt_step_index=step,
            lap_trigger=trigger,
        )
    ]
    activity = CanonicalActivity(
        start_time_utc=START,
        utc_offset_s=0,
        sport="running",
        sub_sport="generic",
        name=None,
        duration_s=3600.0,
        moving_duration_s=2700.0,
        distance_m=10000.0,
        elevation_gain_m=None,
        max_altitude_m=None,
        calories=None,
        device=None,
        laps=laps,
    )
    return CanonicalBatch(kind="activity", activity=activity)


def _run(engine, archive_root: Path, batch: CanonicalBatch) -> int:  # type: ignore[no-untyped-def]
    with (
        patch("perseverer.backfill_lap_workout_steps.parse_fit", return_value=batch),
        engine.connect() as conn,
    ):
        count = backfill_lap_workout_steps(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        return count


def test_fills_step_and_trigger_and_is_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    with engine.connect() as conn:
        _seed_activity_with_laps(conn, archive_root, activity_id="a1", raw_object_id=1)
        conn.commit()

    assert _run(engine, archive_root, _batch(step=2, trigger="manual")) == 1
    with engine.connect() as conn:
        row = conn.execute(select(lap).where(lap.c.activity_id == "a1")).fetchone()
    assert row is not None
    assert (row.wkt_step_index, row.lap_trigger) == (2, "manual")
    assert row.moving_duration_s is None  # nothing else is touched

    assert _run(engine, archive_root, _batch(step=2, trigger="manual")) == 0


def test_leaves_laps_alone_when_the_file_has_neither(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    archive_root = tmp_path / "archive"
    with engine.connect() as conn:
        _seed_activity_with_laps(conn, archive_root, activity_id="a1", raw_object_id=1)
        conn.commit()

    assert _run(engine, archive_root, _batch(step=None, trigger=None)) == 0


def test_lap_trigger_keeps_the_decoded_name_and_missing_stays_none() -> None:
    assert _lap_trigger("distance") == "distance"
    assert _lap_trigger(None) is None
