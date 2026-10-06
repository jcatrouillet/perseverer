"""Tests for backfill_workouts.py -- the targeted, no-full-rebuild backfill for
activity_workout/activity_workout_step. `parse_fit` itself is exhaustively covered by
tests/fit/test_parser.py; these tests monkeypatch it to a controlled result so they don't need a
real FIT binary, and instead focus on this module's own actual job: mapping raw_object rows to
the right activity via activity_source_link, and the insert/skip logic.
"""

from __future__ import annotations

import datetime as dt
import gzip
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import Connection, Engine, select

from perseverer.backfill_workouts import backfill_workouts
from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_source_link,
    activity_workout,
    activity_workout_step,
    athlete,
    metadata,
    raw_object,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import (
    CanonicalActivity,
    CanonicalBatch,
    ParsedWorkout,
    ParsedWorkoutStep,
)


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


def _seed_activity_with_raw_object(
    conn: Connection, archive_root: Path, *, activity_id: str, raw_object_id: int
) -> None:
    start = dt.datetime(2026, 5, 13, 1, 16, 38)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start,
            utc_offset_s=0,
            local_date="2026-05-12",
            sport="running",
            sub_sport="generic",
            duration_s=1800.0,
            distance_m=5000.0,
            primary_source="fit_folder",
            created_at=start,
            updated_at=start,
        )
    )
    storage_path = f"aa/{raw_object_id}.gz"
    (archive_root / "aa").mkdir(parents=True, exist_ok=True)
    with gzip.open(archive_root / storage_path, "wb") as f:
        f.write(b"irrelevant -- parse_fit is monkeypatched")
    conn.execute(
        raw_object.insert().values(
            id=raw_object_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="fit_folder",
            source_locator="whatever.fit",
            kind="fit_activity",
            sha256=f"sha-{raw_object_id}",
            storage_path=storage_path,
            byte_size=1,
            fetched_at=start,
            created_at=start,
        )
    )
    conn.execute(
        activity_source_link.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            source="fit_folder",
            external_id=f"ext-{raw_object_id}",
            raw_object_id=raw_object_id,
            ingested_at=start,
        )
    )


def _batch_with_workout() -> CanonicalBatch:
    workout = ParsedWorkout(
        name="W9 Tue · 5x1km Threshold",
        description="Focus: Lactate threshold.",
        steps=[
            ParsedWorkoutStep(
                step_index=0,
                duration_type="time",
                duration_time_s=900.0,
                duration_distance_m=None,
                target_type="speed",
                target_low_mps=2.439,
                target_high_mps=2.597,
                intensity="warmup",
                repeat_from_step=None,
                repeat_count=None,
            ),
        ],
    )
    a = CanonicalActivity(
        start_time_utc=dt.datetime(2026, 5, 13, 1, 16, 38),
        utc_offset_s=0,
        sport="running",
        sub_sport="generic",
        name=None,
        duration_s=1800.0,
        moving_duration_s=1800.0,
        distance_m=5000.0,
        elevation_gain_m=None,
        max_altitude_m=None,
        calories=None,
        device=None,
        workout=workout,
    )
    return CanonicalBatch(kind="activity", activity=a)


def _batch_with_no_workout() -> CanonicalBatch:
    a = CanonicalActivity(
        start_time_utc=dt.datetime(2026, 5, 13, 1, 16, 38),
        utc_offset_s=0,
        sport="running",
        sub_sport="generic",
        name=None,
        duration_s=1800.0,
        moving_duration_s=1800.0,
        distance_m=5000.0,
        elevation_gain_m=None,
        max_altitude_m=None,
        calories=None,
        device=None,
        workout=None,
    )
    return CanonicalBatch(kind="activity", activity=a)


class TestBackfillWorkouts:
    def test_inserts_a_workout_and_its_steps_for_a_matched_activity(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        archive_root = tmp_path / "archive"
        with engine.connect() as conn:
            _seed_activity_with_raw_object(conn, archive_root, activity_id="a1", raw_object_id=1)
            conn.commit()

        with (
            patch("perseverer.backfill_workouts.parse_fit", return_value=_batch_with_workout()),
            engine.connect() as conn,
        ):
            count = backfill_workouts(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

        assert count == 1
        with engine.connect() as conn:
            workout_row = conn.execute(
                select(activity_workout).where(activity_workout.c.activity_id == "a1")
            ).fetchone()
            step_rows = conn.execute(
                select(activity_workout_step).where(activity_workout_step.c.activity_id == "a1")
            ).fetchall()
        assert workout_row is not None
        assert workout_row.name == "W9 Tue · 5x1km Threshold"
        assert len(step_rows) == 1
        assert step_rows[0].target_low_mps == 2.439

    def test_does_not_touch_the_activity_or_any_other_table(self, tmp_path: Path) -> None:
        """The whole point: purely additive, no mutation of `activity` itself."""
        engine = _engine(tmp_path)
        archive_root = tmp_path / "archive"
        with engine.connect() as conn:
            _seed_activity_with_raw_object(conn, archive_root, activity_id="a1", raw_object_id=1)
            conn.commit()
            before = conn.execute(select(activity.c.updated_at)).scalar_one()

        with (
            patch("perseverer.backfill_workouts.parse_fit", return_value=_batch_with_workout()),
            engine.connect() as conn,
        ):
            backfill_workouts(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

        with engine.connect() as conn:
            after = conn.execute(select(activity.c.updated_at)).scalar_one()
        assert after == before

    def test_is_idempotent_on_a_second_run(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        archive_root = tmp_path / "archive"
        with engine.connect() as conn:
            _seed_activity_with_raw_object(conn, archive_root, activity_id="a1", raw_object_id=1)
            conn.commit()

        with patch("perseverer.backfill_workouts.parse_fit", return_value=_batch_with_workout()):
            with engine.connect() as conn:
                first = backfill_workouts(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)
                conn.commit()
            with engine.connect() as conn:
                second = backfill_workouts(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)
                conn.commit()

        assert first == 1
        assert second == 0
        with engine.connect() as conn:
            rows = conn.execute(select(activity_workout)).fetchall()
        assert len(rows) == 1  # not duplicated

    def test_skips_an_activity_with_no_workout_in_its_raw_fit(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        archive_root = tmp_path / "archive"
        with engine.connect() as conn:
            _seed_activity_with_raw_object(conn, archive_root, activity_id="a1", raw_object_id=1)
            conn.commit()

        with (
            patch("perseverer.backfill_workouts.parse_fit", return_value=_batch_with_no_workout()),
            engine.connect() as conn,
        ):
            count = backfill_workouts(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

        assert count == 0
        with engine.connect() as conn:
            rows = conn.execute(select(activity_workout)).fetchall()
        assert rows == []

    def test_skips_a_raw_object_with_no_matching_activity_source_link(self, tmp_path: Path) -> None:
        """A raw_object that was superseded/never linked (e.g. a merge-losing duplicate) must
        not crash the backfill -- just skip it."""
        engine = _engine(tmp_path)
        archive_root = tmp_path / "archive"
        (archive_root / "aa").mkdir(parents=True)
        storage_path = "aa/999.gz"
        with gzip.open(archive_root / storage_path, "wb") as f:
            f.write(b"orphaned")
        with engine.connect() as conn:
            conn.execute(
                raw_object.insert().values(
                    id=999,
                    athlete_id=DEFAULT_ATHLETE_ID,
                    source="fit_folder",
                    source_locator="orphan.fit",
                    kind="fit_activity",
                    sha256="sha-999",
                    storage_path=storage_path,
                    byte_size=1,
                    fetched_at=dt.datetime(2026, 1, 1),
                    created_at=dt.datetime(2026, 1, 1),
                )
            )
            conn.commit()

        with (
            patch("perseverer.backfill_workouts.parse_fit", return_value=_batch_with_workout()),
            engine.connect() as conn,
        ):
            count = backfill_workouts(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)

        assert count == 0
