"""Tests for performance_curve.py: `best_window_over_stream` (the sliding-window primitive --
see module docstring for why this is the entire correctness of the feature, tested exhaustively
and in isolation from any DB/Parquet I/O below), and `compute_performance_curve`'s own
orchestration (cross-activity combination, sport scoping, trim-window application, reference
value passthrough) against real synthetic Parquet fixtures + an in-memory DuckDB connection.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
from sqlalchemy import Connection, Engine

from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_stream,
    activity_trim_override,
    athlete,
    metadata,
    performance_daily_rollup,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.fit.types import StreamPoint
from perseverer.performance_curve import (
    DURATION_BUCKETS_S,
    best_window_over_stream,
    compute_performance_curve,
)
from perseverer.streams import write_activity_stream


class TestBestWindowOverStream:
    def test_finds_the_exact_average_for_a_uniform_stream(self) -> None:
        # 10 samples at 1Hz, constant HR of 150 -- any 5s window averages exactly 150.
        timestamps = list(range(10))
        values = [150.0] * 10
        assert best_window_over_stream(timestamps, values, 5, mode="mean") == 150.0

    def test_picks_the_highest_mean_window_not_the_first_or_last(self) -> None:
        # A clear HR peak in the middle of the stream -- the best 3s window must be centered on
        # it, not wherever the scan happens to start/end.
        timestamps = list(range(10))
        values = [100.0, 100.0, 100.0, 180.0, 190.0, 185.0, 100.0, 100.0, 100.0, 100.0]
        best = best_window_over_stream(timestamps, values, 3, mode="mean")
        assert best is not None
        assert abs(best - (180.0 + 190.0 + 185.0) / 3) < 1e-9

    def test_rate_mode_finds_the_fastest_window_over_a_cumulative_distance_array(self) -> None:
        # 1Hz, cumulative distance: fast 4 m/s for the first half, slow 1 m/s for the second.
        timestamps = list(range(11))
        cumulative = [0.0]
        for i in range(10):
            cumulative.append(cumulative[-1] + (4.0 if i < 5 else 1.0))
        best = best_window_over_stream(timestamps, cumulative, 4, mode="rate")
        assert best is not None
        assert abs(best - 4.0) < 1e-9  # the fastest 4s window is entirely inside the fast half

    def test_none_when_the_stream_is_shorter_than_the_requested_duration(self) -> None:
        timestamps = list(range(5))  # spans 4s
        values = [150.0] * 5
        assert best_window_over_stream(timestamps, values, 10, mode="mean") is None

    def test_a_large_gap_disqualifies_the_window_it_falls_inside(self) -> None:
        # Samples at t=0..4, then a 30s gap, then t=35..39 -- a would-be 10s window spanning the
        # gap must be rejected; the two 5-sample runs are each too short for a 10s window anyway,
        # so no valid window exists at all here.
        timestamps = [0, 1, 2, 3, 4, 34, 35, 36, 37, 38]
        values = [150.0] * 10
        assert best_window_over_stream(timestamps, values, 10, mode="mean", max_gap_s=15.0) is None

    def test_a_gap_under_the_threshold_is_tolerated(self) -> None:
        # A single 10s gap, under the default 15s max_gap_s -- the window spanning it still
        # counts (this app's own explicit, adjustable policy: small gaps don't disqualify).
        timestamps = [0, 1, 2, 3, 4, 14, 15, 16, 17, 18]
        values = [100.0, 100.0, 100.0, 100.0, 100.0, 200.0, 200.0, 200.0, 200.0, 200.0]
        best = best_window_over_stream(timestamps, values, 18, mode="mean", max_gap_s=15.0)
        assert best is not None

    def test_disqualifies_only_the_windows_that_actually_contain_the_gap(self) -> None:
        # A big gap partway through -- a short-duration window entirely *before* the gap must
        # still be found correctly (the gap doesn't poison the whole stream, just windows that
        # actually straddle it).
        timestamps = [0, 1, 2, 3, 4, 5, 40, 41, 42, 43, 44, 45]
        values = [
            100.0, 100.0, 100.0, 100.0, 190.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0,
        ]
        best = best_window_over_stream(timestamps, values, 1, mode="mean", max_gap_s=15.0)
        assert best == 190.0

    def test_rejects_a_window_whose_actual_span_is_too_far_from_the_target(self) -> None:
        # Only two samples, 100s apart -- there is no real 10s window here at all (the nearest
        # achievable span is 100s, wildly outside the +-10% tolerance), so this must return None
        # rather than fabricating an answer from data that doesn't actually cover the duration.
        timestamps = [0, 100]
        values = [100.0, 200.0]
        assert best_window_over_stream(timestamps, values, 10, mode="mean") is None

    def test_duration_buckets_are_ascending_and_cover_one_second_to_two_hours(self) -> None:
        assert list(DURATION_BUCKETS_S) == sorted(DURATION_BUCKETS_S)
        assert DURATION_BUCKETS_S[0] == 1
        assert DURATION_BUCKETS_S[-1] == 7200

    def test_mismatched_lengths_return_none_rather_than_crash(self) -> None:
        assert best_window_over_stream([0, 1, 2], [1.0, 2.0], 1, mode="mean") is None

    def test_too_short_stream_returns_none(self) -> None:
        assert best_window_over_stream([0.0], [1.0], 1, mode="mean") is None


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


def _seed_activity_with_stream(
    conn: Connection,
    parquet_dir: Path,
    *,
    activity_id: str,
    local_date: str,
    sport: str,
    start: dt.datetime,
    values: dict[str, list[float]],
    athlete_id: str = DEFAULT_ATHLETE_ID,
) -> None:
    n = len(next(iter(values.values())))
    points = [
        StreamPoint(
            timestamp_utc=start + dt.timedelta(seconds=i),
            values={k: v[i] for k, v in values.items()},
        )
        for i in range(n)
    ]
    relative_path, n_samples, channels = write_activity_stream(
        parquet_dir, athlete_id, activity_id, points
    )
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=athlete_id,
            start_time_utc=start,
            utc_offset_s=0,
            local_date=local_date,
            sport=sport,
            duration_s=float(n - 1),
            primary_source="fit_folder",
            created_at=start,
            updated_at=start,
        )
    )
    conn.execute(
        activity_stream.insert().values(
            activity_id=activity_id,
            athlete_id=athlete_id,
            parquet_path=relative_path,
            n_samples=n_samples,
            channels=json.dumps(channels),
        )
    )
    conn.commit()


class TestComputePerformanceCurve:
    def test_combines_the_best_across_activities_and_records_which_one_won(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        start = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity_with_stream(
                conn, parquet_dir,
                activity_id="a1", local_date="2026-06-01", sport="running", start=start,
                values={"heart_rate": [140.0] * 30},
            )
            _seed_activity_with_stream(
                conn, parquet_dir,
                activity_id="a2", local_date="2026-06-02", sport="running",
                start=start + dt.timedelta(days=1),
                values={"heart_rate": [160.0] * 30},  # genuinely higher -- must win
            )

        with engine.connect() as conn:
            curve = compute_performance_curve(
                conn, duckdb.connect(":memory:"), parquet_dir,
                athlete_id=DEFAULT_ATHLETE_ID, metric="heart_rate",
                start_date=dt.date(2026, 6, 1), end_date=dt.date(2026, 6, 3),
                sports=["running"], as_of=dt.date(2026, 6, 3),
            )

        point = next(p for p in curve.points if p.duration_s == 15)
        assert point.value == 160.0
        assert point.activity_id == "a2"

    def test_pace_and_gap_ignore_the_sports_param_and_hardcode_running(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        start = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity_with_stream(
                conn, parquet_dir,
                activity_id="run1", local_date="2026-06-01", sport="running", start=start,
                values={
                    "distance_m": [i * 4.0 for i in range(30)],
                    "altitude_m": [100.0] * 30,
                },
            )
            # A cycling activity, much faster -- must NOT be picked up by pace/gap even though
            # "sports" isn't restricted to running here.
            _seed_activity_with_stream(
                conn, parquet_dir,
                activity_id="ride1", local_date="2026-06-02", sport="cycling",
                start=start + dt.timedelta(days=1),
                values={
                    "distance_m": [i * 12.0 for i in range(30)],
                    "altitude_m": [100.0] * 30,
                },
            )

        with engine.connect() as conn:
            curve = compute_performance_curve(
                conn, duckdb.connect(":memory:"), parquet_dir,
                athlete_id=DEFAULT_ATHLETE_ID, metric="pace",
                start_date=dt.date(2026, 6, 1), end_date=dt.date(2026, 6, 3),
                sports=None, as_of=dt.date(2026, 6, 3),
            )

        point = next(p for p in curve.points if p.duration_s == 15)
        assert point.activity_id == "run1"  # the (slower) run, never the faster ride

    def test_heart_rate_sport_filter_excludes_unselected_sports(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        start = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
        with engine.connect() as conn:
            _seed_activity_with_stream(
                conn, parquet_dir,
                activity_id="ride1", local_date="2026-06-01", sport="cycling", start=start,
                values={"heart_rate": [170.0] * 30},
            )

        with engine.connect() as conn:
            curve = compute_performance_curve(
                conn, duckdb.connect(":memory:"), parquet_dir,
                athlete_id=DEFAULT_ATHLETE_ID, metric="heart_rate",
                start_date=dt.date(2026, 6, 1), end_date=dt.date(2026, 6, 3),
                sports=["running"],  # cycling not selected
                as_of=dt.date(2026, 6, 3),
            )

        assert curve.points == []

    def test_available_false_shaped_as_empty_points_when_nothing_qualifies(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        with engine.connect() as conn:
            curve = compute_performance_curve(
                conn, duckdb.connect(":memory:"), parquet_dir,
                athlete_id=DEFAULT_ATHLETE_ID, metric="pace",
                start_date=dt.date(2026, 6, 1), end_date=dt.date(2026, 6, 3),
                sports=None, as_of=dt.date(2026, 6, 3),
            )
        assert curve.points == []
        assert curve.threshold_pace_s_per_km is None

    def test_reference_values_are_passed_through_for_the_active_metric_only(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        with engine.connect() as conn:
            conn.execute(
                performance_daily_rollup.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    local_date="2026-06-03",
                    threshold_pace_s_per_km=300.0,
                    aerobic_threshold_pace_s_per_km=340.0,
                    threshold_hr_bpm=165.0,
                    aerobic_threshold_hr_bpm=145.0,
                    max_hr_bpm=190.0,
                    refreshed_at=dt.datetime(2026, 6, 3),
                )
            )
            conn.commit()

        with engine.connect() as conn:
            pace_curve = compute_performance_curve(
                conn, duckdb.connect(":memory:"), parquet_dir,
                athlete_id=DEFAULT_ATHLETE_ID, metric="pace",
                start_date=dt.date(2026, 6, 1), end_date=dt.date(2026, 6, 3),
                sports=None, as_of=dt.date(2026, 6, 3),
            )
            hr_curve = compute_performance_curve(
                conn, duckdb.connect(":memory:"), parquet_dir,
                athlete_id=DEFAULT_ATHLETE_ID, metric="heart_rate",
                start_date=dt.date(2026, 6, 1), end_date=dt.date(2026, 6, 3),
                sports=["running"], as_of=dt.date(2026, 6, 3),
            )

        assert pace_curve.threshold_pace_s_per_km == 300.0
        assert pace_curve.aerobic_threshold_pace_s_per_km == 340.0
        assert pace_curve.threshold_hr_bpm is None  # not this metric's own field
        assert hr_curve.threshold_hr_bpm == 165.0
        assert hr_curve.aerobic_threshold_hr_bpm == 145.0
        assert hr_curve.max_hr_bpm == 190.0
        assert hr_curve.threshold_pace_s_per_km is None

    def test_a_trimmed_activitys_out_of_window_data_is_excluded(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        start = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
        with engine.connect() as conn:
            # First 10s at a very high HR (simulating car travel before the walk actually
            # started); the trim excludes it. Without honoring the trim, this would set a
            # nonsensical short-duration HR record.
            _seed_activity_with_stream(
                conn, parquet_dir,
                activity_id="hike1", local_date="2026-06-01", sport="hiking", start=start,
                values={"heart_rate": [200.0] * 10 + [120.0] * 20},
            )
            conn.execute(
                activity_trim_override.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_start_time_utc=start,
                    trim_start_s=10.0,
                    trim_end_s=None,
                    created_at=start,
                    updated_at=start,
                )
            )
            conn.commit()

        with engine.connect() as conn:
            curve = compute_performance_curve(
                conn, duckdb.connect(":memory:"), parquet_dir,
                athlete_id=DEFAULT_ATHLETE_ID, metric="heart_rate",
                start_date=dt.date(2026, 6, 1), end_date=dt.date(2026, 6, 1),
                sports=["hiking"], as_of=dt.date(2026, 6, 1),
            )

        point = next(p for p in curve.points if p.duration_s == 5)
        assert point.value == 120.0  # never the trimmed-out 200.0 stretch
