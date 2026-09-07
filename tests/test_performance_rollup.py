"""Tests for performance_rollup.refresh_performance_rollup: the 42-day rolling-max VDOT / 365-day
rolling-max HR windows, threshold-pace/HR derivation (empirical + fallback), race-time
predictions, causality, and idempotency. See that module's own docstring for the full model.
"""

import datetime as dt
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_metric,
    athlete,
    metadata,
    metric_definition,
    performance_daily_rollup,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.gap import AVG_GAP_METRIC_KEY
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.performance_rollup import refresh_performance_rollup
from perseverer.vdot import compute_threshold_pace_s_per_km

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
    conn: Connection, *, activity_id: str, local_date: str, sport: str = "running"
) -> None:
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date=local_date,
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


def _row(conn: Connection, local_date: str):  # type: ignore[no-untyped-def]
    return conn.execute(
        select(performance_daily_rollup).where(
            performance_daily_rollup.c.athlete_id == DEFAULT_ATHLETE_ID,
            performance_daily_rollup.c.local_date == local_date,
        )
    ).fetchone()


def test_no_activities_is_a_no_op(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        rows = conn.execute(select(performance_daily_rollup)).fetchall()
    assert rows == []


def test_rolling_vdot_is_the_42_day_trailing_maximum(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_metric(conn, activity_id="a0", metric_key=VDOT_METRIC_KEY, value=45.0)
        _add_activity(conn, activity_id="a1", local_date="2025-06-10")
        _add_metric(conn, activity_id="a1", metric_key=VDOT_METRIC_KEY, value=50.0)
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        # On 2025-06-15, both runs are within the trailing 42 days -- the max (50) wins.
        row = _row(conn, "2025-06-15")
        assert row is not None
        assert row.rolling_vdot == 50.0

        # 43 days after a0 (2025-06-01), a0 has aged out but a1 (2025-06-10) hasn't yet.
        row = _row(conn, "2025-07-13")
        assert row is not None
        assert row.rolling_vdot == 50.0

        # 43 days after a1 (2025-06-10) with nothing newer -- both have aged out, None.
        row = _row(conn, "2025-07-22")
        assert row is not None
        assert row.rolling_vdot is None


def test_max_hr_rolls_over_365_days_and_includes_non_running_sports(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-01-01", sport="cycling")
        _add_metric(
            conn, activity_id="a0", metric_key=_MAX_HR_KEY, value=180.0, source="fit_folder"
        )
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        # Within the 365-day window.
        row = _row(conn, "2025-06-01")
        assert row is not None
        assert row.max_hr_bpm == 180.0
        assert row.max_hr_source == "empirical"

        # More than 365 days later -- aged out, and no birthdate configured -- stays null exactly
        # like before the formula fallback existed.
        row = _row(conn, "2026-06-01")
        assert row is not None
        assert row.max_hr_bpm is None
        assert row.max_hr_source is None


def test_max_hr_falls_back_to_tanaka_formula_once_empirical_data_ages_out(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(birthdate="1990-01-01")
        )
        _add_activity(conn, activity_id="a0", local_date="2025-01-01", sport="cycling")
        _add_metric(
            conn, activity_id="a0", metric_key=_MAX_HR_KEY, value=180.0, source="fit_folder"
        )
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        # Still within the 365-day empirical window -- real data wins, no fallback.
        row = _row(conn, "2025-06-01")
        assert row is not None
        assert row.max_hr_bpm == 180.0
        assert row.max_hr_source == "empirical"

        # Aged out -- falls back to Tanaka (208 - 0.7*age) using the configured birthdate.
        row = _row(conn, "2026-06-01")
        assert row is not None
        assert row.max_hr_source == "formula_fallback"
        age_years = (dt.date(2026, 6, 1) - dt.date(1990, 1, 1)).days / 365.25
        assert row.max_hr_bpm == 208.0 - 0.7 * age_years


def test_max_hr_stays_null_with_no_empirical_data_and_no_birthdate(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        # A VDOT-bearing run with no HR data at all -- rollup rows exist, but nothing feeds
        # max_hr_bpm, and there's no birthdate to fall back to.
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_metric(conn, activity_id="a0", metric_key=VDOT_METRIC_KEY, value=45.0)
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        row = _row(conn, "2025-06-01")
    assert row is not None
    assert row.max_hr_bpm is None
    assert row.max_hr_source is None


def test_threshold_pace_matches_the_closed_form_value(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_metric(conn, activity_id="a0", metric_key=VDOT_METRIC_KEY, value=50.0)
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        row = _row(conn, "2025-06-01")
    assert row is not None
    assert row.threshold_pace_s_per_km == compute_threshold_pace_s_per_km(50.0)
    # Race predictions are populated whenever rolling_vdot is present.
    assert row.predicted_5k_s is not None
    assert row.predicted_marathon_s is not None


def test_threshold_hr_uses_empirical_median_when_enough_samples_exist(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="vdot0", local_date="2025-06-01")
        _add_metric(conn, activity_id="vdot0", metric_key=VDOT_METRIC_KEY, value=50.0)
        threshold_pace = compute_threshold_pace_s_per_km(50.0)
        assert threshold_pace is not None
        threshold_speed_mps = 1000.0 / threshold_pace
        # Three runs at threshold pace (within tolerance), HRs 150/155/160 -> median 155.
        for i, hr in enumerate([150.0, 155.0, 160.0]):
            aid = f"near{i}"
            _add_activity(conn, activity_id=aid, local_date=f"2025-06-0{2 + i}")
            _add_metric(conn, activity_id=aid, metric_key=VDOT_METRIC_KEY, value=45.0)
            _add_metric(
                conn,
                activity_id=aid,
                metric_key=AVG_GAP_METRIC_KEY,
                value=threshold_speed_mps,
            )
            _add_metric(
                conn, activity_id=aid, metric_key=_AVG_HR_KEY, value=hr, source="fit_folder"
            )
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        row = _row(conn, "2025-06-10")
    assert row is not None
    assert row.threshold_hr_bpm == 155.0
    assert row.threshold_hr_source == "empirical"


def test_threshold_hr_falls_back_to_fraction_of_max_hr_when_too_few_samples(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_metric(conn, activity_id="a0", metric_key=VDOT_METRIC_KEY, value=50.0)
        _add_activity(conn, activity_id="a1", local_date="2025-06-02")
        _add_metric(
            conn, activity_id="a1", metric_key=_MAX_HR_KEY, value=190.0, source="fit_folder"
        )
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        row = _row(conn, "2025-06-10")
    assert row is not None
    assert row.threshold_hr_source == "fallback"
    assert row.threshold_hr_bpm == 0.88 * 190.0


def test_a_later_activity_never_changes_an_earlier_days_row(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_metric(conn, activity_id="a0", metric_key=VDOT_METRIC_KEY, value=45.0)
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        before = _row(conn, "2025-06-01")
        assert before is not None
        assert before.rolling_vdot == 45.0

        # A much faster run several days later must not retroactively change 2025-06-01's row.
        _add_activity(conn, activity_id="a1", local_date="2025-06-05")
        _add_metric(conn, activity_id="a1", metric_key=VDOT_METRIC_KEY, value=70.0)
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        after = _row(conn, "2025-06-01")
    assert after is not None
    assert after.rolling_vdot == 45.0


def test_refresh_is_idempotent_full_recompute_no_duplicate_rows(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_metric(conn, activity_id="a0", metric_key=VDOT_METRIC_KEY, value=45.0)
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()
        rows = conn.execute(
            select(performance_daily_rollup).where(
                performance_daily_rollup.c.local_date == "2025-06-01"
            )
        ).fetchall()
    assert len(rows) == 1
