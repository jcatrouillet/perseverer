"""Tests for pace_bands.py: the pure per-sample bucketing function, and refresh_pace_bands's
running-only scoping, full-recompute-clears-stale-rows behavior, and skip conditions."""

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_metric, activity_stream, athlete, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import StreamPoint
from perseverer.pace_bands import PACE_BANDS, compute_pace_band_seconds, refresh_pace_bands
from perseverer.streams import write_activity_stream


def _band_key(suffix: str) -> str:
    return next(b for b in PACE_BANDS if b.suffix == suffix).metric_key


def _timestamps(n: int, *, step_s: int = 10) -> list[dt.datetime]:
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    return [start + dt.timedelta(seconds=i * step_s) for i in range(n)]


class TestComputePaceBandSeconds:
    def test_a_steady_pace_lands_entirely_in_one_band(self) -> None:
        # 1000/300 = 3.333 m/s = 5:00/km exactly, held for 100 seconds across 11 samples.
        totals = compute_pace_band_seconds(_timestamps(11), [1000 / 300] * 11)
        assert totals[_band_key("5_00_5_30")] == 100.0
        assert sum(totals.values()) == 100.0

    def test_splits_time_across_bands_when_pace_changes_mid_run(self) -> None:
        # First half comfortably inside "4:00-4:30" (4:15/km = 255s/km), second half comfortably
        # inside "7:00-7:30" (7:15/km = 435s/km) -- clear of both bands' own edges, so this isn't
        # sensitive to the speed<->seconds/km round-trip's own floating-point rounding the way an
        # exact-boundary pace would be.
        speeds = [1000 / 255] * 10 + [1000 / 435] * 11
        totals = compute_pace_band_seconds(_timestamps(21), speeds)
        assert totals[_band_key("4_00_4_30")] == 100.0
        assert totals[_band_key("7_00_7_30")] == 100.0

    def test_stationary_samples_are_excluded_not_bucketed(self) -> None:
        # Below the 0.3 m/s stationary floor throughout.
        totals = compute_pace_band_seconds(_timestamps(3), [0.1, 0.1, 0.1])
        assert sum(totals.values()) == 0.0

    def test_a_null_speed_sample_is_excluded(self) -> None:
        totals = compute_pace_band_seconds(_timestamps(3), [1000 / 300, None, 1000 / 300])
        # Only the first sample's 10s interval is counted -- the None sample contributes nothing.
        assert totals[_band_key("5_00_5_30")] == 10.0

    def test_a_large_gap_is_capped_not_attributed_wholesale(self) -> None:
        timestamps = [
            dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
            dt.datetime(2026, 1, 1, tzinfo=dt.UTC) + dt.timedelta(seconds=600),  # 10-minute gap
        ]
        totals = compute_pace_band_seconds(timestamps, [1000 / 300, 1000 / 300])
        assert totals[_band_key("5_00_5_30")] == 10.0  # capped, not 600.0

    def test_implausibly_slow_pace_lands_in_walk(self) -> None:
        totals = compute_pace_band_seconds(_timestamps(2), [1000 / 900, 1000 / 900])  # 15:00/km
        assert totals[_band_key("walk")] == 10.0


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


def _add_activity(conn: Connection, *, activity_id: str, sport: str = "running") -> None:
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date="2026-01-01",
            sport=sport,
            distance_m=5000.0,
            moving_duration_s=1800.0,
            primary_source="fit_folder",
            created_at=now,
            updated_at=now,
        )
    )


def _add_speed_stream(
    parquet_dir: Path, conn: Connection, *, activity_id: str, speeds_mps: list[float]
) -> None:
    points = [
        StreamPoint(
            timestamp_utc=dt.datetime(2026, 1, 1, tzinfo=dt.UTC) + dt.timedelta(seconds=i * 10),
            values={"speed_mps": speed},
        )
        for i, speed in enumerate(speeds_mps)
    ]
    relative_path, n_samples, channels = write_activity_stream(
        parquet_dir, DEFAULT_ATHLETE_ID, activity_id, points
    )
    conn.execute(
        activity_stream.insert().values(
            activity_id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            parquet_path=relative_path,
            n_samples=n_samples,
            channels=json.dumps(channels),
        )
    )


class TestRefreshPaceBands:
    def test_writes_band_rows_only_for_running_activities_with_a_speed_stream(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        speeds = [1000 / 300] * 11
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run_with_stream", sport="running")
            _add_speed_stream(parquet_dir, conn, activity_id="run_with_stream", speeds_mps=speeds)
            _add_activity(conn, activity_id="run_no_stream", sport="running")
            _add_activity(conn, activity_id="ride_with_stream", sport="cycling")
            _add_speed_stream(parquet_dir, conn, activity_id="ride_with_stream", speeds_mps=speeds)
            conn.commit()

            refresh_pace_bands(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            rows = conn.execute(
                select(
                    activity_metric.c.activity_id,
                    activity_metric.c.metric_key,
                    activity_metric.c.value_num,
                )
            ).fetchall()
            assert [r.activity_id for r in rows] == ["run_with_stream"]
            assert rows[0].metric_key == _band_key("5_00_5_30")
            assert rows[0].value_num == 100.0

    def test_skips_a_stream_with_no_speed_channel(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run1")
            points = [
                StreamPoint(timestamp_utc=ts, values={"heart_rate": 140.0}) for ts in _timestamps(5)
            ]
            relative_path, n_samples, channels = write_activity_stream(
                parquet_dir, DEFAULT_ATHLETE_ID, "run1", points
            )
            conn.execute(
                activity_stream.insert().values(
                    activity_id="run1",
                    athlete_id=DEFAULT_ATHLETE_ID,
                    parquet_path=relative_path,
                    n_samples=n_samples,
                    channels=json.dumps(channels),
                )
            )
            conn.commit()

            written = refresh_pace_bands(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            assert written == 0

    def test_full_recompute_clears_stale_rows_for_an_activity_no_longer_running(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run1", sport="running")
            _add_speed_stream(parquet_dir, conn, activity_id="run1", speeds_mps=[1000 / 300] * 11)
            conn.commit()
            refresh_pace_bands(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            conn.execute(activity.update().where(activity.c.id == "run1").values(sport="cycling"))
            conn.commit()

            refresh_pace_bands(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            rows = conn.execute(select(activity_metric.c.id)).fetchall()
            assert rows == []

    def test_idempotent_across_repeated_calls(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run1")
            _add_speed_stream(parquet_dir, conn, activity_id="run1", speeds_mps=[1000 / 300] * 11)
            conn.commit()

            refresh_pace_bands(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()
            refresh_pace_bands(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            rows = conn.execute(select(activity_metric.c.id)).fetchall()
            assert len(rows) == 1
