"""Tests for gap.py: the pure distance-weighted average-GAP function, and refresh_avg_gap's
running-only scoping, full-recompute-clears-stale-rows behavior, and skip conditions -- same
shape as test_pace_bands.py/test_performance.py, the two closest existing analogs (same EAV
activity_metric storage, same full-recompute-on-ingest contract).
"""

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_metric, activity_stream, athlete, metadata
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import StreamPoint
from perseverer.gap import AVG_GAP_METRIC_KEY, compute_avg_gap_speed_mps, refresh_avg_gap
from perseverer.streams import write_activity_stream


def _timestamps(n: int, *, step_s: int = 10) -> list[dt.datetime]:
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    return [start + dt.timedelta(seconds=i * step_s) for i in range(n)]


class TestComputeAvgGapSpeedMps:
    def test_flat_ground_gap_equals_actual_speed(self) -> None:
        # 10 intervals of 10s at 4 m/s -> 40m/interval, zero grade throughout.
        distances = [i * 40.0 for i in range(11)]
        altitudes = [100.0] * 11
        gap = compute_avg_gap_speed_mps(_timestamps(11), distances, altitudes)
        assert gap is not None
        assert abs(gap - 4.0) < 1e-9

    def test_uphill_gap_speed_is_faster_than_actual_speed(self) -> None:
        # Same 4 m/s actual pace, but climbing steeply (Minetti's cost rises with positive
        # grade) -- the extra effort is "worth more", so the grade-adjusted equivalent flat
        # speed comes out higher than the raw 4 m/s actually covered.
        distances = [i * 40.0 for i in range(11)]
        altitudes = [i * 8.0 for i in range(11)]  # 8m climb per 40m -> 20% grade
        gap = compute_avg_gap_speed_mps(_timestamps(11), distances, altitudes)
        assert gap is not None
        assert gap > 4.0

    def test_downhill_gap_speed_is_slower_than_actual_speed(self) -> None:
        # A gentle, economical descent (Minetti's cost is lowest around -10 to -20% grade) --
        # the same 4 m/s actual pace reflects less real effort, so GAP reports it slower.
        distances = [i * 40.0 for i in range(11)]
        altitudes = [i * -4.0 for i in range(11)]  # -10% grade
        gap = compute_avg_gap_speed_mps(_timestamps(11), distances, altitudes)
        assert gap is not None
        assert gap < 4.0

    def test_stationary_intervals_are_excluded(self) -> None:
        # Below the 0.3 m/s stationary floor throughout (1m per 10s = 0.1 m/s).
        distances = [i * 1.0 for i in range(5)]
        altitudes = [100.0] * 5
        assert compute_avg_gap_speed_mps(_timestamps(5), distances, altitudes) is None

    def test_none_when_a_channel_is_entirely_missing(self) -> None:
        distances = [i * 40.0 for i in range(11)]
        altitudes: list[float | None] = [None] * 11
        assert compute_avg_gap_speed_mps(_timestamps(11), distances, altitudes) is None

    def test_intervals_missing_one_channel_are_skipped_not_fatal(self) -> None:
        # First half has no altitude reading (a gap in the barometric channel); second half is
        # a clean flat 4 m/s -- the result should reflect only the usable second half.
        distances = [i * 40.0 for i in range(11)]
        altitudes: list[float | None] = [None] * 6 + [100.0] * 5
        gap = compute_avg_gap_speed_mps(_timestamps(11), distances, altitudes)
        assert gap is not None
        assert abs(gap - 4.0) < 1e-9

    def test_non_increasing_distance_interval_is_skipped(self) -> None:
        # A GPS/footpod glitch that reports distance going backward or flat must not divide by
        # a non-positive delta.
        distances = [0.0, 40.0, 40.0, 80.0]  # the middle interval has zero delta
        altitudes = [100.0] * 4
        gap = compute_avg_gap_speed_mps(_timestamps(4, step_s=10), distances, altitudes)
        assert gap is not None
        assert abs(gap - 4.0) < 1e-9


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


def _add_gap_stream(
    parquet_dir: Path,
    conn: Connection,
    *,
    activity_id: str,
    distances_m: list[float],
    altitudes_m: list[float],
) -> None:
    points = [
        StreamPoint(
            timestamp_utc=dt.datetime(2026, 1, 1, tzinfo=dt.UTC) + dt.timedelta(seconds=i * 10),
            values={"distance_m": d, "altitude_m": a},
        )
        for i, (d, a) in enumerate(zip(distances_m, altitudes_m, strict=True))
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


class TestRefreshAvgGap:
    def test_writes_rows_only_for_running_activities_with_altitude_and_distance(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        distances = [i * 40.0 for i in range(11)]
        altitudes = [100.0] * 11
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run_with_stream", sport="running")
            _add_gap_stream(
                parquet_dir, conn, activity_id="run_with_stream",
                distances_m=distances, altitudes_m=altitudes,
            )
            _add_activity(conn, activity_id="run_no_stream", sport="running")
            _add_activity(conn, activity_id="ride_with_stream", sport="cycling")
            _add_gap_stream(
                parquet_dir, conn, activity_id="ride_with_stream",
                distances_m=distances, altitudes_m=altitudes,
            )
            conn.commit()

            written = refresh_avg_gap(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            assert written == 1
            rows = conn.execute(
                select(activity_metric.c.activity_id, activity_metric.c.value_num)
            ).fetchall()
            assert [r.activity_id for r in rows] == ["run_with_stream"]
            assert rows[0].value_num is not None
            assert abs(rows[0].value_num - 4.0) < 1e-9

    def test_skips_a_stream_with_no_altitude_channel(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run1")
            points = [
                StreamPoint(timestamp_utc=ts, values={"distance_m": i * 10.0})
                for i, ts in enumerate(_timestamps(5))
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

            written = refresh_avg_gap(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            assert written == 0

    def test_full_recompute_clears_stale_rows_for_an_activity_no_longer_running(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        distances = [i * 40.0 for i in range(11)]
        altitudes = [100.0] * 11
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run1", sport="running")
            _add_gap_stream(
                parquet_dir, conn, activity_id="run1",
                distances_m=distances, altitudes_m=altitudes,
            )
            conn.commit()
            refresh_avg_gap(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            conn.execute(activity.update().where(activity.c.id == "run1").values(sport="cycling"))
            conn.commit()

            refresh_avg_gap(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            rows = conn.execute(select(activity_metric.c.id)).fetchall()
            assert rows == []

    def test_idempotent_across_repeated_calls(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        distances = [i * 40.0 for i in range(11)]
        altitudes = [100.0] * 11
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run1")
            _add_gap_stream(
                parquet_dir, conn, activity_id="run1",
                distances_m=distances, altitudes_m=altitudes,
            )
            conn.commit()

            refresh_avg_gap(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()
            refresh_avg_gap(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            rows = conn.execute(select(activity_metric.c.id)).fetchall()
            assert len(rows) == 1
            assert rows[0].id is not None

    def test_registers_the_metric_definition(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        distances = [i * 40.0 for i in range(11)]
        altitudes = [100.0] * 11
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run1")
            _add_gap_stream(
                parquet_dir, conn, activity_id="run1",
                distances_m=distances, altitudes_m=altitudes,
            )
            conn.commit()

            refresh_avg_gap(conn, parquet_dir, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            row = conn.execute(
                select(activity_metric.c.metric_key, activity_metric.c.unit)
                .where(activity_metric.c.activity_id == "run1")
            ).fetchone()
            assert row is not None
            assert row.metric_key == AVG_GAP_METRIC_KEY
            assert row.unit == "m/s"
