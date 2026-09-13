"""Tests for threshold_analysis.compute_threshold_factor_analysis: reusing vo2max_analysis's own
driving-activity answer for both threshold paces, the empirical-contributors/median-flagging
logic for both threshold HRs, and the max-HR-driving-activity lookup for the fallback path.
"""

import datetime as dt
from pathlib import Path

from sqlalchemy import Connection, Engine

from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_metric, athlete, metadata, metric_definition
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.gap import AVG_GAP_METRIC_KEY
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.performance_rollup import (
    AEROBIC_THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR,
    THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR,
    refresh_performance_rollup,
)
from perseverer.threshold_analysis import compute_threshold_factor_analysis
from perseverer.vdot import AEROBIC_THRESHOLD_VO2MAX_FRACTION, compute_threshold_pace_s_per_km

_AVG_HR_KEY = "fit.session.avg_heart_rate"
_MAX_HR_KEY = "fit.session.max_heart_rate"


def _engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=now,
            )
        )
        for key in (VDOT_METRIC_KEY, AVG_GAP_METRIC_KEY, _AVG_HR_KEY, _MAX_HR_KEY):
            conn.execute(
                metric_definition.insert().values(
                    metric_key=key,
                    display_name=key,
                    category="performance",
                    value_type="numeric",
                    first_seen_at=now,
                    first_seen_source="perseverer",
                )
            )
        conn.commit()
    return engine


def _add_activity(
    conn: Connection,
    *,
    activity_id: str,
    local_date: str,
    name: str | None = "Run",
    sport: str = "running",
) -> None:
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date=local_date,
            name=name,
            sport=sport,
            duration_s=1800.0,
            moving_duration_s=1700.0,
            distance_m=5000.0,
            primary_source="fit_folder",
            created_at=now,
            updated_at=now,
        )
    )


def _add_metric(
    conn: Connection, *, activity_id: str, metric_key: str, value: float, source: str = "perseverer"
) -> None:
    conn.execute(
        activity_metric.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            metric_key=metric_key,
            value_num=value,
            source=source,
            created_at=dt.datetime.now(dt.UTC),
        )
    )


def test_no_data_reports_the_gap_through_both_thresholds(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        result = compute_threshold_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 15)
        )
    assert result.vo2max.driving_activity is None
    assert result.anaerobic_threshold_pace_s_per_km is None
    assert result.aerobic_threshold_pace_s_per_km is None
    assert result.anaerobic_threshold_hr.threshold_hr_bpm is None
    assert any("No threshold pace" in m for m in result.anaerobic_threshold_hr.missing)


def test_both_threshold_paces_share_the_same_vo2max_driving_activity(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01", name="Tempo run")
        _add_metric(conn, activity_id="a0", metric_key=VDOT_METRIC_KEY, value=50.0)
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        result = compute_threshold_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 10)
        )
    assert result.vo2max.driving_activity is not None
    assert result.vo2max.driving_activity.activity_id == "a0"
    assert result.anaerobic_threshold_pace_s_per_km == compute_threshold_pace_s_per_km(50.0)
    assert result.aerobic_threshold_pace_s_per_km == compute_threshold_pace_s_per_km(
        50.0, fraction=AEROBIC_THRESHOLD_VO2MAX_FRACTION
    )
    # Both are derived from exactly the same rolling_vdot / driving activity -- not two answers.
    assert result.anaerobic_threshold_hr.reference_pace_s_per_km == (
        result.anaerobic_threshold_pace_s_per_km
    )
    assert result.aerobic_threshold_hr.reference_pace_s_per_km == (
        result.aerobic_threshold_pace_s_per_km
    )


def test_empirical_threshold_hr_lists_contributors_and_flags_the_median(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="vdot0", local_date="2025-06-01")
        _add_metric(conn, activity_id="vdot0", metric_key=VDOT_METRIC_KEY, value=50.0)
        threshold_pace = compute_threshold_pace_s_per_km(50.0)
        assert threshold_pace is not None
        threshold_speed_mps = 1000.0 / threshold_pace
        for i, hr in enumerate([150.0, 155.0, 160.0]):
            aid = f"near{i}"
            _add_activity(conn, activity_id=aid, local_date=f"2025-06-0{2 + i}", name=f"Run {i}")
            _add_metric(conn, activity_id=aid, metric_key=VDOT_METRIC_KEY, value=45.0)
            _add_metric(
                conn, activity_id=aid, metric_key=AVG_GAP_METRIC_KEY, value=threshold_speed_mps
            )
            _add_metric(
                conn, activity_id=aid, metric_key=_AVG_HR_KEY, value=hr, source="fit_folder"
            )
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        result = compute_threshold_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 10)
        )
    breakdown = result.anaerobic_threshold_hr
    assert breakdown.threshold_hr_bpm == 155.0
    assert breakdown.threshold_hr_source == "empirical"
    assert [c.avg_hr_bpm for c in breakdown.contributors] == [150.0, 155.0, 160.0]
    assert [c.is_median for c in breakdown.contributors] == [False, True, False]
    assert breakdown.missing == []


def test_empirical_threshold_hr_with_even_contributor_count_flags_both_middle_runs(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="vdot0", local_date="2025-06-01")
        _add_metric(conn, activity_id="vdot0", metric_key=VDOT_METRIC_KEY, value=50.0)
        threshold_pace = compute_threshold_pace_s_per_km(50.0)
        assert threshold_pace is not None
        threshold_speed_mps = 1000.0 / threshold_pace
        for i, hr in enumerate([150.0, 155.0, 160.0, 165.0]):
            aid = f"near{i}"
            _add_activity(conn, activity_id=aid, local_date=f"2025-06-0{2 + i}")
            _add_metric(conn, activity_id=aid, metric_key=VDOT_METRIC_KEY, value=45.0)
            _add_metric(
                conn, activity_id=aid, metric_key=AVG_GAP_METRIC_KEY, value=threshold_speed_mps
            )
            _add_metric(
                conn, activity_id=aid, metric_key=_AVG_HR_KEY, value=hr, source="fit_folder"
            )
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        result = compute_threshold_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 12)
        )
    breakdown = result.anaerobic_threshold_hr
    assert breakdown.threshold_hr_bpm == 157.5  # (155 + 160) / 2
    assert [c.is_median for c in breakdown.contributors] == [False, True, True, False]


def test_fallback_threshold_hr_reports_the_max_hr_driving_activity(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_metric(conn, activity_id="a0", metric_key=VDOT_METRIC_KEY, value=50.0)
        _add_activity(
            conn, activity_id="a1", local_date="2025-06-02", name="Hard bike ride", sport="cycling"
        )
        _add_metric(
            conn, activity_id="a1", metric_key=_MAX_HR_KEY, value=190.0, source="fit_folder"
        )
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        result = compute_threshold_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 10)
        )
    breakdown = result.anaerobic_threshold_hr
    assert breakdown.threshold_hr_source == "fallback"
    assert breakdown.threshold_hr_bpm == THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR * 190.0
    assert breakdown.contributors == []
    assert breakdown.max_hr_driving_activity is not None
    assert breakdown.max_hr_driving_activity.activity_id == "a1"
    assert breakdown.max_hr_driving_activity.name == "Hard bike ride"
    assert any("using" in m for m in breakdown.missing)

    aerobic_breakdown = result.aerobic_threshold_hr
    assert aerobic_breakdown.threshold_hr_bpm == (
        AEROBIC_THRESHOLD_HR_FALLBACK_FRACTION_OF_MAX_HR * 190.0
    )
    assert aerobic_breakdown.max_hr_driving_activity is not None
    assert aerobic_breakdown.max_hr_driving_activity.activity_id == "a1"


def test_fallback_from_formula_max_hr_has_no_driving_activity(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(birthdate="1990-01-01")
        )
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_metric(conn, activity_id="a0", metric_key=VDOT_METRIC_KEY, value=50.0)
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        result = compute_threshold_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 10)
        )
    breakdown = result.anaerobic_threshold_hr
    assert breakdown.threshold_hr_source == "fallback"
    assert result.max_hr_source == "formula_fallback"
    # No real activity behind a formula-derived max HR.
    assert breakdown.max_hr_driving_activity is None


def test_aerobic_and_anaerobic_breakdowns_use_independent_qualifying_windows(
    tmp_path: Path,
) -> None:
    """Runs near the anaerobic threshold pace must not leak into the aerobic breakdown's own
    contributors and vice versa -- the two operate at very different, non-overlapping paces."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="vdot0", local_date="2025-06-01")
        _add_metric(conn, activity_id="vdot0", metric_key=VDOT_METRIC_KEY, value=50.0)

        anaerobic_pace = compute_threshold_pace_s_per_km(50.0)
        aerobic_pace = compute_threshold_pace_s_per_km(
            50.0, fraction=AEROBIC_THRESHOLD_VO2MAX_FRACTION
        )
        assert anaerobic_pace is not None
        assert aerobic_pace is not None

        for i, hr in enumerate([150.0, 155.0, 160.0]):
            aid = f"anaerobic{i}"
            _add_activity(conn, activity_id=aid, local_date=f"2025-06-0{2 + i}")
            _add_metric(conn, activity_id=aid, metric_key=VDOT_METRIC_KEY, value=45.0)
            _add_metric(
                conn, activity_id=aid, metric_key=AVG_GAP_METRIC_KEY, value=1000.0 / anaerobic_pace
            )
            _add_metric(
                conn, activity_id=aid, metric_key=_AVG_HR_KEY, value=hr, source="fit_folder"
            )
        for i, hr in enumerate([120.0, 125.0, 130.0]):
            aid = f"aerobic{i}"
            _add_activity(conn, activity_id=aid, local_date=f"2025-06-1{2 + i}")
            _add_metric(conn, activity_id=aid, metric_key=VDOT_METRIC_KEY, value=35.0)
            _add_metric(
                conn, activity_id=aid, metric_key=AVG_GAP_METRIC_KEY, value=1000.0 / aerobic_pace
            )
            _add_metric(
                conn, activity_id=aid, metric_key=_AVG_HR_KEY, value=hr, source="fit_folder"
            )
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        result = compute_threshold_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 20)
        )
    assert result.anaerobic_threshold_hr.threshold_hr_bpm == 155.0
    assert result.aerobic_threshold_hr.threshold_hr_bpm == 125.0
    anaerobic_ids = {c.activity_id for c in result.anaerobic_threshold_hr.contributors}
    aerobic_ids = {c.activity_id for c in result.aerobic_threshold_hr.contributors}
    assert anaerobic_ids.isdisjoint(aerobic_ids)
