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
from perseverer.gap import (
    AVG_GAP_METRIC_KEY,
    _cost_of_running,
    _grade_adjusted_time_factor,
    _windowed_grade,
    compute_avg_gap_speed_mps,
    compute_lap_gap_speeds_mps,
    refresh_avg_gap,
)
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

    def test_noisy_but_net_flat_altitude_does_not_produce_a_fast_bias(self) -> None:
        """Regression test for a confirmed real bug: summing raw 1Hz sample-to-sample altitude
        deltas over a real run with ~52m of true elevation gain/loss produced ~123m of implied
        gain -- roughly 58% GPS/barometric noise -- and because Minetti's cost curve is steeper
        uphill than the downhill curve is generous, that *symmetric* noise around a near-zero
        true grade produced a systematic *fast* bias (every rep of a real interval session
        landed 7-14s/km faster than intervals.icu's own smoothed GAP). Reproduced here: a
        genuinely flat course (net elevation change zero), 1Hz sampling (matching real device
        data), with alternating +/-3m noise every sample -- without windowing this would read
        measurably faster than the true 4 m/s; with `_windowed_grade` smoothing it over a real
        +/-15s time window, it should read indistinguishable from the true flat speed."""
        n = 61
        distances = [i * 4.0 for i in range(n)]  # 4 m/s, 1s steps
        altitudes = [100.0 + (3.0 if i % 2 == 0 else -3.0) for i in range(n)]
        gap = compute_avg_gap_speed_mps(_timestamps(n, step_s=1), distances, altitudes)
        assert gap is not None
        # 4 m/s actual pace; a real fast bias from unsmoothed noise would read measurably above
        # this (confirmed against this exact fixture before the windowing fix landed).
        assert abs(gap - 4.0) < 0.1


class TestWindowedGrade:
    def test_flat_ground_is_zero(self) -> None:
        distances = [i * 20.0 for i in range(11)]
        altitudes = [100.0] * 11
        assert _windowed_grade(_timestamps(11), distances, altitudes, 5) == 0.0

    def test_steady_uphill_matches_the_true_grade_regardless_of_window(self) -> None:
        # A perfectly linear ramp -- windowing a noiseless signal shouldn't distort it at all,
        # any more than reading the raw endpoints would.
        distances = [i * 20.0 for i in range(11)]
        altitudes = [i * 2.0 for i in range(11)]  # 2m per 20m -> 10% grade throughout
        grade = _windowed_grade(_timestamps(11), distances, altitudes, 5)
        assert grade is not None
        assert abs(grade - 0.10) < 1e-9

    def test_alternating_noise_around_flat_averages_to_near_zero(self) -> None:
        distances = [i * 20.0 for i in range(11)]
        altitudes = [100.0 + (3.0 if i % 2 == 0 else -3.0) for i in range(11)]
        grade = _windowed_grade(_timestamps(11), distances, altitudes, 5)
        assert grade is not None
        assert abs(grade) < 0.02  # near-zero, not the wild per-sample swings raw deltas show

    def test_none_when_a_window_edge_has_no_distance(self) -> None:
        # The window is keyed on time, so only the two *edge* points' own distance/altitude
        # matter -- a None strictly between them (never selected as an edge) doesn't propagate.
        distances: list[float | None] = [0.0, 20.0, None, 60.0, None]
        altitudes: list[float | None] = [100.0] * 5
        # 10s spacing, +/-15s half-window -> right edge lands on index 4, which has no distance.
        assert _windowed_grade(_timestamps(5, step_s=10), distances, altitudes, 2) is None

    def test_a_missing_value_strictly_inside_the_window_does_not_propagate(self) -> None:
        # Confirms the flip side of the test above: only the two edge points are read, so a gap
        # in between (a dropped barometric sample, say) doesn't sink the whole window.
        distances: list[float | None] = [0.0, 20.0, None, 60.0, 80.0]
        altitudes: list[float | None] = [100.0] * 5
        grade = _windowed_grade(_timestamps(5, step_s=10), distances, altitudes, 2)
        assert grade == 0.0

    def test_narrows_to_available_data_near_the_stream_edges(self) -> None:
        # Near index 0, the window can only look forward -- shouldn't return None just because
        # the left side is bounded by the array start rather than half_window_s.
        distances = [i * 20.0 for i in range(11)]
        altitudes = [100.0] * 11
        assert _windowed_grade(_timestamps(11), distances, altitudes, 0) == 0.0

    def test_a_fast_segment_still_gets_the_full_time_window(self) -> None:
        """The bug a fixed-*distance* window has: it covers less real time the faster a segment
        is run, under-smoothing exactly the fastest (VO2/threshold) reps where noise bias matters
        most. A *time* window doesn't have this problem -- prove it by checking a fast segment's
        window still reaches multiple points away, not just its immediate neighbours."""
        # 1s spacing, ~5.7 m/s (a hard interval pace) -- a 25m distance half-window would only
        # reach ~4 points either side; a 15s time half-window reaches all 15.
        distances = [i * 5.7 for i in range(31)]
        altitudes = [100.0] * 31
        # Confirm indirectly: alternating noise here should still average to near-zero, which
        # only happens if the window is wide enough (in sample count) to average several cycles.
        altitudes = [100.0 + (3.0 if i % 2 == 0 else -3.0) for i in range(31)]
        grade = _windowed_grade(_timestamps(31, step_s=1), distances, altitudes, 15)
        assert grade is not None
        assert abs(grade) < 0.02


def _epoch_s(n: int, *, step_s: int = 10, start_s: float = 0.0) -> list[float]:
    return [start_s + i * step_s for i in range(n)]


class TestComputeLapGapSpeedsMps:
    """Boundary behavior only -- the grade-adjusted math itself is already covered by
    TestComputeAvgGapSpeedMps above, which this function reuses via start_idx/end_idx bounds
    over the *full* stream (not a per-lap slice -- see the next test for why that distinction
    is load-bearing, not just an implementation detail)."""

    def test_flat_ground_each_lap_matches_its_own_raw_speed(self) -> None:
        # 11 points, 0-100s, 4 m/s throughout, flat. Two laps split at 50s.
        stream_epoch_s = _epoch_s(11)
        distances = [i * 40.0 for i in range(11)]
        altitudes = [100.0] * 11
        result = compute_lap_gap_speeds_mps([0.0, 50.0], stream_epoch_s, distances, altitudes)
        assert len(result) == 2
        assert result[0] is not None and abs(result[0] - 4.0) < 1e-9
        assert result[1] is not None and abs(result[1] - 4.0) < 1e-9

    def test_final_lap_extends_to_the_streams_last_point(self) -> None:
        # First half at 4 m/s, second half at 8 m/s -- laps split exactly at the speed change,
        # so each lap's own GAP should reflect only its own half.
        stream_epoch_s = _epoch_s(11)  # 0..100s
        distances = [i * 40.0 for i in range(6)] + [200.0 + (i + 1) * 80.0 for i in range(5)]
        altitudes = [100.0] * 11
        result = compute_lap_gap_speeds_mps([0.0, 50.0], stream_epoch_s, distances, altitudes)
        assert result[0] is not None and abs(result[0] - 4.0) < 1e-9
        assert result[1] is not None and abs(result[1] - 8.0) < 1e-9

    def test_matches_compute_avg_gap_speed_mps_on_the_equivalent_slice(self) -> None:
        # Reuses TestComputeAvgGapSpeedMps's own uphill fixture shape as a cross-check that
        # slicing doesn't change the underlying grade math at all.
        stream_epoch_s = _epoch_s(11)
        distances = [i * 40.0 for i in range(11)]
        altitudes = [100.0 + i * 2.0 for i in range(11)]  # steady uphill throughout
        [lap_result] = compute_lap_gap_speeds_mps([0.0], stream_epoch_s, distances, altitudes)
        direct = compute_avg_gap_speed_mps(_timestamps(11), distances, altitudes)
        assert lap_result is not None and direct is not None
        assert abs(lap_result - direct) < 1e-9

    def test_lap_starting_after_the_last_stream_sample_is_none(self) -> None:
        stream_epoch_s = _epoch_s(5)  # 0..40s
        distances = [i * 40.0 for i in range(5)]
        altitudes = [100.0] * 5
        result = compute_lap_gap_speeds_mps([0.0, 999.0], stream_epoch_s, distances, altitudes)
        assert result[0] is not None
        assert result[1] is None

    def test_windowing_uses_full_stream_context_across_a_lap_boundary(self) -> None:
        """Regression test: slicing the stream per lap *before* computing GAP (the original
        implementation) was a confirmed real bug -- a point near a lap's own boundary loses the
        window context that would normally reach past it, biasing exactly the boundary samples a
        coach reading interval reps cares about most. Proven here by showing the real
        (full-stream-context) result differs from what a naive slice-then-average would give for
        the identical lap -- if compute_lap_gap_speeds_mps ever regressed back to slicing first,
        this would start asserting a false equality and fail."""
        n = 21
        distances = [i * 20.0 for i in range(n)]  # 2 m/s
        altitudes = [100.0 + (3.0 if i % 2 == 0 else -3.0) for i in range(n)]
        stream_epoch_s = _epoch_s(n)

        results = compute_lap_gap_speeds_mps([0.0, 100.0], stream_epoch_s, distances, altitudes)
        lap2_full_context = results[1]

        # What the old (buggy) slice-first approach would have computed: only lap 2's own points
        # visible to the windowing search, so its first samples lose their left-side context.
        lap2_sliced_only = compute_avg_gap_speed_mps(
            _timestamps(n - 10, step_s=10), distances[10:], altitudes[10:]
        )

        assert lap2_full_context is not None and lap2_sliced_only is not None
        assert abs(lap2_full_context - lap2_sliced_only) > 1e-6

    def test_mismatched_stream_lengths_return_all_none(self) -> None:
        result = compute_lap_gap_speeds_mps([0.0, 10.0], [0.0, 10.0, 20.0], [0.0, 40.0], [100.0])
        assert result == [None, None]

    def test_empty_stream_returns_all_none(self) -> None:
        result = compute_lap_gap_speeds_mps([0.0, 10.0], [], [], [])
        assert result == [None, None]


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


class TestGradeAdjustedTimeFactor:
    """The softened downhill curve matching Strava's own published post-2017 points -- see
    gap.py's own module docstring and _grade_adjusted_time_factor's docstring for the sourcing.
    Mirrors frontend/src/gap.test.ts's equivalent describe block exactly, same three points."""

    def test_flat_ground_is_unadjusted(self) -> None:
        assert abs(_grade_adjusted_time_factor(0.0) - 1.0) < 1e-9

    def test_minus_9_percent_hits_the_known_dip(self) -> None:
        # factor is a *speed* multiplier of 0.88 there, so the time multiplier is 1/0.88.
        assert abs(_grade_adjusted_time_factor(-0.09) - 1 / 0.88) < 1e-9

    def test_minus_18_percent_is_fully_recovered(self) -> None:
        assert abs(_grade_adjusted_time_factor(-0.18) - 1.0) < 1e-9

    def test_gentler_than_pure_minetti_at_minus_10_percent(self) -> None:
        softened = _grade_adjusted_time_factor(-0.1)
        pure_minetti = 3.6 / _cost_of_running(-0.1)
        assert softened < pure_minetti

    def test_stays_flat_beyond_minus_18_percent(self) -> None:
        assert abs(_grade_adjusted_time_factor(-0.3) - 1.0) < 1e-9
        assert abs(_grade_adjusted_time_factor(-0.9) - 1.0) < 1e-9


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
