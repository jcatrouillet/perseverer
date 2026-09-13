"""Tests for vo2max_analysis.compute_vo2max_factor_analysis: which run drives the current
rolling-max VO2max, what else qualified in the window, when the driving run ages out, and the
gap/staleness diagnostics. Reuses test_performance_rollup.py's own fixture helpers so the window
math is exercised against the exact same shape of data that module's own tests already cover.
"""

import datetime as dt
from pathlib import Path

from sqlalchemy import Connection, Engine

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
from perseverer.performance import VDOT_METRIC_KEY
from perseverer.performance_rollup import ROLLING_VDOT_WINDOW_DAYS, refresh_performance_rollup
from perseverer.vo2max_analysis import compute_vo2max_factor_analysis


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
        conn.execute(
            metric_definition.insert().values(
                metric_key=VDOT_METRIC_KEY,
                display_name=VDOT_METRIC_KEY,
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
    name: str | None = "Morning run",
    distance_m: float = 5000.0,
    moving_duration_s: float = 1700.0,
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
            sport="running",
            duration_s=moving_duration_s + 100.0,
            moving_duration_s=moving_duration_s,
            distance_m=distance_m,
            primary_source="fit_folder",
            created_at=now,
            updated_at=now,
        )
    )


def _add_vdot(conn: Connection, *, activity_id: str, value: float) -> None:
    conn.execute(
        activity_metric.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            activity_id=activity_id,
            metric_key=VDOT_METRIC_KEY,
            value_num=value,
            source="perseverer",
            created_at=dt.datetime.now(dt.UTC),
        )
    )


def test_no_qualifying_run_reports_the_gap_explicitly(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        result = compute_vo2max_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 15)
        )
    assert result.rolling_vdot is None
    assert result.driving_activity is None
    assert result.other_contributors == []
    assert result.expires_on is None
    assert any("No qualifying run yet" in m for m in result.missing)


def test_single_run_is_the_driving_activity_with_no_other_contributors(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01", name="Tempo run")
        _add_vdot(conn, activity_id="a0", value=48.0)
        conn.commit()
        result = compute_vo2max_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 10)
        )
    assert result.rolling_vdot == 48.0
    assert result.driving_activity is not None
    assert result.driving_activity.activity_id == "a0"
    assert result.driving_activity.name == "Tempo run"
    assert result.other_contributors == []
    assert result.expires_on == (
        dt.date(2025, 6, 1) + dt.timedelta(days=ROLLING_VDOT_WINDOW_DAYS)
    ).isoformat()
    assert any("single qualifying run" in m for m in result.missing)


def test_the_highest_vdot_run_in_window_drives_the_value_others_listed_separately(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="easy", local_date="2025-06-01", name="Easy jog")
        _add_vdot(conn, activity_id="easy", value=40.0)
        _add_activity(conn, activity_id="race", local_date="2025-06-05", name="5k race")
        _add_vdot(conn, activity_id="race", value=55.0)
        _add_activity(conn, activity_id="tempo", local_date="2025-06-08", name="Tempo")
        _add_vdot(conn, activity_id="tempo", value=50.0)
        conn.commit()
        result = compute_vo2max_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 10)
        )
    assert result.rolling_vdot == 55.0
    assert result.driving_activity is not None
    assert result.driving_activity.activity_id == "race"
    other_ids = [c.activity_id for c in result.other_contributors]
    assert other_ids == ["tempo", "easy"]  # sorted descending by VDOT, driving run excluded


def test_activities_outside_the_window_are_excluded(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="old", local_date="2025-01-01")
        _add_vdot(conn, activity_id="old", value=60.0)
        _add_activity(conn, activity_id="recent", local_date="2025-06-05")
        _add_vdot(conn, activity_id="recent", value=45.0)
        conn.commit()
        result = compute_vo2max_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 10)
        )
    assert result.rolling_vdot == 45.0
    assert result.driving_activity is not None
    assert result.driving_activity.activity_id == "recent"


def test_expiring_soon_warns_when_the_driving_run_is_about_to_age_out(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_vdot(conn, activity_id="a0", value=50.0)
        conn.commit()
        expires_on = dt.date(2025, 6, 1) + dt.timedelta(days=ROLLING_VDOT_WINDOW_DAYS)

        # A few days before it ages out -- should warn.
        near_expiry = expires_on - dt.timedelta(days=3)
        result = compute_vo2max_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=near_expiry
        )
        assert any("ages out" in m for m in result.missing)

        # Well before that -- no such warning yet.
        far_from_expiry = expires_on - dt.timedelta(days=30)
        result = compute_vo2max_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=far_from_expiry
        )
        assert not any("ages out" in m for m in result.missing)


def test_stale_effort_warns_after_half_the_window_with_no_new_qualifying_run(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_vdot(conn, activity_id="a0", value=50.0)
        conn.commit()

        stale_as_of = dt.date(2025, 6, 1) + dt.timedelta(days=ROLLING_VDOT_WINDOW_DAYS // 2)
        result = compute_vo2max_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=stale_as_of
        )
        assert result.days_since_last_qualifying_run == ROLLING_VDOT_WINDOW_DAYS // 2
        assert any("days ago" in m for m in result.missing)


def test_rolling_vdot_matches_the_stored_performance_rollup_value(tmp_path: Path) -> None:
    """This is a read of the same underlying facts through a different lens, not a second
    computation that could drift from performance_daily_rollup.rolling_vdot -- pin that down
    directly rather than just asserting it by reading the two modules' code side by side."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_vdot(conn, activity_id="a0", value=45.0)
        _add_activity(conn, activity_id="a1", local_date="2025-06-10")
        _add_vdot(conn, activity_id="a1", value=50.0)
        conn.commit()
        refresh_performance_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID)
        conn.commit()

        as_of = dt.date(2025, 6, 15)
        rollup_row = conn.execute(
            performance_daily_rollup.select().where(
                performance_daily_rollup.c.athlete_id == DEFAULT_ATHLETE_ID,
                performance_daily_rollup.c.local_date == as_of.isoformat(),
            )
        ).fetchone()
        assert rollup_row is not None

        result = compute_vo2max_factor_analysis(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=as_of)
    assert result.rolling_vdot == rollup_row.rolling_vdot == 50.0


def test_deleted_activity_is_excluded(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a0", local_date="2025-06-01")
        _add_vdot(conn, activity_id="a0", value=50.0)
        conn.execute(
            activity.update()
            .where(activity.c.id == "a0")
            .values(deleted_at=dt.datetime.now(dt.UTC))
        )
        conn.commit()
        result = compute_vo2max_factor_analysis(
            conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2025, 6, 10)
        )
    assert result.driving_activity is None
    assert result.rolling_vdot is None
