"""Tests for running_load.py: the pure rTSS formula, and refresh_running_tss's config-gating,
running-only scoping, full-recompute-clears-stale-rows behavior, and its dependency on
gap.py::refresh_avg_gap having already run -- same shape as test_gap.py/test_pace_bands.py, the
closest existing analogs (same EAV activity_metric storage, same full-recompute contract).
"""

import datetime as dt
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_metric,
    athlete,
    athlete_running_load_config,
    metadata,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.gap import AVG_GAP_METRIC_KEY
from perseverer.metrics.registry import get_or_register_metric
from perseverer.running_load import (
    RUNNING_TSS_METRIC_KEY,
    compute_running_tss,
    refresh_running_tss,
)


class TestComputeRunningTss:
    def test_one_hour_exactly_at_threshold_pace_is_100(self) -> None:
        # 5:00/km threshold -> 1000/300 = 3.3333 m/s. Running exactly that speed for 1 hour
        # (3600s) should be the canonical "100" reference point of the whole rTSS scale.
        threshold_pace = 300.0
        threshold_speed = 1000.0 / threshold_pace
        rtss = compute_running_tss(3600.0, threshold_speed, threshold_pace)
        assert rtss is not None
        assert abs(rtss - 100.0) < 1e-9

    def test_half_speed_for_one_hour_is_quarter_load(self) -> None:
        # IF = 0.5 -> rTSS = 1 * 0.5^2 * 100 = 25.
        threshold_pace = 300.0
        threshold_speed = 1000.0 / threshold_pace
        rtss = compute_running_tss(3600.0, threshold_speed / 2, threshold_pace)
        assert rtss is not None
        assert abs(rtss - 25.0) < 1e-9

    def test_double_duration_doubles_load_at_constant_intensity(self) -> None:
        threshold_pace = 300.0
        threshold_speed = 1000.0 / threshold_pace
        one_hour = compute_running_tss(3600.0, threshold_speed, threshold_pace)
        two_hours = compute_running_tss(7200.0, threshold_speed, threshold_pace)
        assert one_hour is not None and two_hours is not None
        assert abs(two_hours - 2 * one_hour) < 1e-9

    def test_none_when_duration_missing_or_non_positive(self) -> None:
        assert compute_running_tss(None, 3.0, 300.0) is None
        assert compute_running_tss(0.0, 3.0, 300.0) is None
        assert compute_running_tss(-1.0, 3.0, 300.0) is None

    def test_none_when_avg_gap_speed_missing_or_non_positive(self) -> None:
        assert compute_running_tss(3600.0, None, 300.0) is None
        assert compute_running_tss(3600.0, 0.0, 300.0) is None

    def test_none_when_threshold_pace_missing_or_non_positive(self) -> None:
        assert compute_running_tss(3600.0, 3.0, None) is None
        assert compute_running_tss(3600.0, 3.0, 0.0) is None


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


def _add_activity(
    conn: Connection, *, activity_id: str, sport: str = "running", moving_duration_s: float = 3600.0
) -> None:
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date="2026-01-01",
            sport=sport,
            distance_m=10000.0,
            moving_duration_s=moving_duration_s,
            primary_source="fit_folder",
            created_at=now,
            updated_at=now,
        )
    )


def _add_avg_gap(conn: Connection, *, activity_id: str, value_mps: float) -> None:
    get_or_register_metric(
        conn,
        metric_key=AVG_GAP_METRIC_KEY,
        source="perseverer",
        display_name="Average Grade Adjusted Pace",
        unit_si="m/s",
        category="performance",
        value_type="numeric",
    )
    conn.execute(
        activity_metric.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            metric_key=AVG_GAP_METRIC_KEY,
            value_num=value_mps,
            source="perseverer",
            created_at=dt.datetime.now(dt.UTC),
        )
    )


def _set_threshold_pace(conn: Connection, *, threshold_pace_sec_per_km: float | None) -> None:
    conn.execute(
        athlete_running_load_config.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            threshold_pace_sec_per_km=threshold_pace_sec_per_km,
            updated_at=dt.datetime.now(dt.UTC),
        )
    )


class TestRefreshRunningTss:
    def test_no_config_row_is_a_no_op(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run1")
            _add_avg_gap(conn, activity_id="run1", value_mps=3.5)
            conn.commit()

            written = refresh_running_tss(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            assert written == 0
            rows = conn.execute(
                select(activity_metric).where(
                    activity_metric.c.metric_key == RUNNING_TSS_METRIC_KEY
                )
            ).fetchall()
            assert rows == []

    def test_null_threshold_pace_is_also_a_no_op(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run1")
            _add_avg_gap(conn, activity_id="run1", value_mps=3.5)
            _set_threshold_pace(conn, threshold_pace_sec_per_km=None)
            conn.commit()

            written = refresh_running_tss(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            assert written == 0

    def test_writes_rtss_for_running_activity_with_avg_gap(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            # Threshold pace 5:00/km == 3.3333 m/s; this run's avg_gap is exactly threshold ->
            # 1h at IF=1.0 -> rTSS == 100.
            _add_activity(conn, activity_id="run1", moving_duration_s=3600.0)
            _add_avg_gap(conn, activity_id="run1", value_mps=1000.0 / 300.0)
            _set_threshold_pace(conn, threshold_pace_sec_per_km=300.0)
            conn.commit()

            written = refresh_running_tss(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            assert written == 1
            row = conn.execute(
                select(activity_metric).where(
                    activity_metric.c.metric_key == RUNNING_TSS_METRIC_KEY
                )
            ).fetchone()
            assert row is not None
            assert row.activity_id == "run1"
            assert row.source == "perseverer"
            assert abs(row.value_num - 100.0) < 1e-9

    def test_skips_running_activity_with_no_avg_gap_row(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run_no_gap")
            _set_threshold_pace(conn, threshold_pace_sec_per_km=300.0)
            conn.commit()

            written = refresh_running_tss(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            assert written == 0

    def test_ignores_non_running_activities(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="ride1", sport="cycling")
            _add_avg_gap(conn, activity_id="ride1", value_mps=8.0)
            _set_threshold_pace(conn, threshold_pace_sec_per_km=300.0)
            conn.commit()

            written = refresh_running_tss(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            assert written == 0

    def test_full_recompute_clears_stale_rows(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            _add_activity(conn, activity_id="run1")
            _add_avg_gap(conn, activity_id="run1", value_mps=1000.0 / 300.0)
            _set_threshold_pace(conn, threshold_pace_sec_per_km=300.0)
            conn.commit()
            refresh_running_tss(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            # Once un-configured (threshold pace cleared), a rerun must remove the stale row --
            # exactly the "inert, byte-identical to before configuration" contract fitness.py's
            # fallback depends on.
            conn.execute(
                athlete_running_load_config.update()
                .where(athlete_running_load_config.c.athlete_id == DEFAULT_ATHLETE_ID)
                .values(threshold_pace_sec_per_km=None)
            )
            conn.commit()

            written = refresh_running_tss(conn, athlete_id=DEFAULT_ATHLETE_ID)
            conn.commit()

            assert written == 0
            rows = conn.execute(
                select(activity_metric).where(
                    activity_metric.c.metric_key == RUNNING_TSS_METRIC_KEY
                )
            ).fetchall()
            assert rows == []
