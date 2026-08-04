"""Tests for rollups.refresh_daily_rollup: correctness of each aggregate, and idempotency of
the delete-then-reinsert recompute model. See docs/adr/0006-phase-3-read-api-and-rollups.md.
"""

import datetime as dt
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from sporthealth.db.engine import make_engine
from sporthealth.db.schema import (
    activity,
    athlete,
    day_rollup,
    health_metric_daily_rollup,
    health_observation,
    metadata,
    metric_definition,
    sleep_session,
)
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.rollups import refresh_daily_rollup


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
    conn: Connection, *, local_date: str, duration_s: float, distance_m: float
) -> None:
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        activity.insert().values(
            id=f"act-{duration_s}-{distance_m}",
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date=local_date,
            sport="running",
            duration_s=duration_s,
            moving_duration_s=duration_s * 0.95,
            distance_m=distance_m,
            elevation_gain_m=10.0,
            calories=200.0,
            primary_source="fit_folder",
            created_at=now,
            updated_at=now,
        )
    )


def _register_metric(conn: Connection, metric_key: str) -> None:
    conn.execute(
        metric_definition.insert().values(
            metric_key=metric_key,
            display_name=metric_key,
            category="health",
            value_type="numeric",
            first_seen_at=dt.datetime.now(dt.UTC),
            first_seen_source="fit_folder",
        )
    )


def _add_observation(
    conn: Connection, *, metric_key: str, local_date: str, value: float, hour: int
) -> None:
    conn.execute(
        health_observation.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            metric_key=metric_key,
            observed_at_utc=dt.datetime(2025, 6, 1, hour, tzinfo=dt.UTC).replace(tzinfo=None),
            local_date=local_date,
            aggregation="daily",
            value_num=value,
            source="fit_folder",
        )
    )


def test_activity_aggregates_sum_across_multiple_activities_same_day(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, local_date="2025-06-01", duration_s=1800.0, distance_m=5000.0)
        _add_activity(conn, local_date="2025-06-01", duration_s=3600.0, distance_m=20000.0)
        conn.commit()

        refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date="2025-06-01")
        conn.commit()

        row = conn.execute(select(day_rollup)).fetchone()

    assert row is not None
    assert row.activity_count == 2
    assert row.activity_duration_s == 5400.0
    assert row.activity_distance_m == 25000.0


def test_health_rollup_aggregates_sum_avg_min_max_last(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _register_metric(conn, "resting_heart_rate")
        _add_observation(
            conn, metric_key="resting_heart_rate", local_date="2025-06-01", value=48.0, hour=6
        )
        _add_observation(
            conn, metric_key="resting_heart_rate", local_date="2025-06-01", value=52.0, hour=20
        )
        conn.commit()

        refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date="2025-06-01")
        conn.commit()

        row = conn.execute(select(health_metric_daily_rollup)).fetchone()

    assert row is not None
    assert row.metric_key == "resting_heart_rate"
    assert row.value_sum == 100.0
    assert row.value_avg == 50.0
    assert row.value_min == 48.0
    assert row.value_max == 52.0
    assert row.value_last == 52.0  # the later (hour=20) observation
    assert row.n_observations == 2


def test_sleep_picks_the_longest_session_when_multiple_sources_exist(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        conn.execute(
            sleep_session.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-01",
                start_time_utc=now,
                end_time_utc=now,
                total_sleep_s=20000.0,
                sleep_score=70.0,
                source="fit_folder",
            )
        )
        conn.execute(
            sleep_session.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-01",
                start_time_utc=now,
                end_time_utc=now,
                total_sleep_s=25000.0,
                sleep_score=80.0,
                source="garmin_export",
            )
        )
        conn.commit()

        refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date="2025-06-01")
        conn.commit()

        row = conn.execute(select(day_rollup)).fetchone()

    assert row is not None
    assert row.sleep_total_s == 25000.0
    assert row.sleep_score == 80.0


def test_refresh_is_idempotent_no_duplicate_rows(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, local_date="2025-06-01", duration_s=1800.0, distance_m=5000.0)
        _register_metric(conn, "resting_heart_rate")
        _add_observation(
            conn, metric_key="resting_heart_rate", local_date="2025-06-01", value=48.0, hour=6
        )
        conn.commit()

        refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date="2025-06-01")
        conn.commit()
        refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date="2025-06-01")
        conn.commit()

        day_rows = conn.execute(select(day_rollup)).fetchall()
        health_rows = conn.execute(select(health_metric_daily_rollup)).fetchall()

    assert len(day_rows) == 1
    assert len(health_rows) == 1


def test_refresh_for_a_date_with_no_data_still_inserts_a_zeroed_day_rollup_row(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date="2025-06-01")
        conn.commit()
        row = conn.execute(select(day_rollup)).fetchone()

    assert row is not None
    assert row.activity_count == 0
    assert row.activity_duration_s is None
    assert row.sleep_total_s is None
