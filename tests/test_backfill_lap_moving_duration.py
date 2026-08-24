"""Tests for backfill_lap_moving_duration.py -- the targeted, no-full-rebuild backfill for
lap.moving_duration_s. `parse_fit` itself is exhaustively covered by tests/fit/test_parser.py;
these tests monkeypatch it to a controlled result so they don't need a real FIT binary, and
instead focus on this module's own actual job: mapping raw_object rows to the right activity via
activity_source_link, and the update/skip logic.
"""

from __future__ import annotations

import datetime as dt
import gzip
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import Connection, Engine, select

from perseverer.backfill_lap_moving_duration import backfill_lap_moving_duration
from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_source_link, athlete, lap, metadata, raw_object
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import CanonicalActivity, CanonicalBatch, ParsedLap


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


def _seed_activity_with_laps(
    conn: Connection, archive_root: Path, *, activity_id: str, raw_object_id: int
) -> None:
    start = dt.datetime(2026, 7, 1, 2, 0, 28)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=start,
            utc_offset_s=0,
            local_date="2026-07-01",
            sport="running",
            sub_sport="generic",
            duration_s=3600.0,
            distance_m=10000.0,
            primary_source="fit_folder",
            created_at=start,
            updated_at=start,
        )
    )
    conn.execute(
        lap.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            lap_index=0,
            start_time_utc=start,
            duration_s=1008.193,
            moving_duration_s=None,
            distance_m=234.04,
            avg_hr=138.0,
            max_hr=150.0,
            avg_speed_mps=0.23,
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


def _batch_with_paused_lap() -> CanonicalBatch:
    # A real recovery-interval lap: 1008.193s elapsed (includes a ~15min device pause) vs
    # 90.0s timer (excludes it) -- transcribed from a real archived FIT file's lap_mesgs.
    laps = [
        ParsedLap(
            lap_index=0,
            start_time_utc=dt.datetime(2026, 7, 1, 2, 0, 28),
            duration_s=1008.193,
            moving_duration_s=90.0,
            distance_m=234.04,
            avg_hr=138.0,
            max_hr=150.0,
            avg_speed_mps=0.23,
        ),
    ]
    a = CanonicalActivity(
        start_time_utc=dt.datetime(2026, 7, 1, 2, 0, 28),
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
    return CanonicalBatch(kind="activity", activity=a)


def _batch_with_no_laps() -> CanonicalBatch:
    a = CanonicalActivity(
        start_time_utc=dt.datetime(2026, 7, 1, 2, 0, 28),
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
    )
    return CanonicalBatch(kind="activity", activity=a)


class TestBackfillLapMovingDuration:
    def test_updates_moving_duration_s_for_a_matched_lap(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        archive_root = tmp_path / "archive"
        with engine.connect() as conn:
            _seed_activity_with_laps(conn, archive_root, activity_id="a1", raw_object_id=1)
            conn.commit()

        with (
            patch(
                "perseverer.backfill_lap_moving_duration.parse_fit",
                return_value=_batch_with_paused_lap(),
            ),
            engine.connect() as conn,
        ):
            count = backfill_lap_moving_duration(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

        assert count == 1
        with engine.connect() as conn:
            lap_row = conn.execute(
                select(lap).where(lap.c.activity_id == "a1", lap.c.lap_index == 0)
            ).fetchone()
        assert lap_row is not None
        assert lap_row.moving_duration_s == 90.0
        assert lap_row.duration_s == 1008.193  # untouched -- only the new column changes

    def test_is_idempotent_on_a_second_run(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        archive_root = tmp_path / "archive"
        with engine.connect() as conn:
            _seed_activity_with_laps(conn, archive_root, activity_id="a1", raw_object_id=1)
            conn.commit()

        with patch(
            "perseverer.backfill_lap_moving_duration.parse_fit",
            return_value=_batch_with_paused_lap(),
        ):
            with engine.connect() as conn:
                first = backfill_lap_moving_duration(
                    conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID
                )
                conn.commit()
            with engine.connect() as conn:
                second = backfill_lap_moving_duration(
                    conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID
                )
                conn.commit()

        assert first == 1
        assert second == 0  # already backfilled -- skipped, not re-updated

    def test_skips_an_activity_with_no_laps_in_its_raw_fit(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        archive_root = tmp_path / "archive"
        with engine.connect() as conn:
            _seed_activity_with_laps(conn, archive_root, activity_id="a1", raw_object_id=1)
            conn.commit()

        with (
            patch(
                "perseverer.backfill_lap_moving_duration.parse_fit",
                return_value=_batch_with_no_laps(),
            ),
            engine.connect() as conn,
        ):
            count = backfill_lap_moving_duration(conn, archive_root, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

        assert count == 0
        with engine.connect() as conn:
            lap_row = conn.execute(select(lap).where(lap.c.activity_id == "a1")).fetchone()
        assert lap_row is not None
        assert lap_row.moving_duration_s is None
