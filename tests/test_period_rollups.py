"""Tests for rollups.refresh_period_rollup: sum-of-sums/weighted-average correctness, and that
week/month rollups automatically reflect a retroactive correction to an already-rolled-up day
-- no watermark or "closed period" concept needed, it falls out of the existing
accumulate-then-refresh contract. See docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
"""

import datetime as dt
from pathlib import Path

from sqlalchemy import Connection, Engine, select

from sporthealth.db.engine import make_engine
from sporthealth.db.schema import (
    activity,
    athlete,
    health_metric_period_rollup,
    health_observation,
    metadata,
    metric_definition,
    period_rollup,
)
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.rollups import (
    month_start,
    refresh_daily_rollup,
    refresh_period_rollup,
    week_start_monday,
)


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
    conn: Connection, *, activity_id: str, local_date: str, distance_m: float
) -> None:
    now = dt.datetime.now(dt.UTC)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date=local_date,
            sport="running",
            duration_s=1800.0,
            moving_duration_s=1700.0,
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
    year, month, day = (int(p) for p in local_date.split("-"))
    conn.execute(
        health_observation.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            metric_key=metric_key,
            observed_at_utc=dt.datetime(year, month, day, hour, tzinfo=dt.UTC).replace(
                tzinfo=None
            ),
            local_date=local_date,
            aggregation="daily",
            value_num=value,
            source="fit_folder",
        )
    )


def test_week_start_monday_and_month_start() -> None:
    assert week_start_monday("2025-06-02") == "2025-06-02"  # already a Monday
    assert week_start_monday("2025-06-04") == "2025-06-02"  # Wednesday -> that week's Monday
    assert week_start_monday("2025-06-08") == "2025-06-02"  # Sunday -> same week's Monday
    assert month_start("2025-06-17") == "2025-06-01"


def test_activity_aggregates_sum_across_days_in_a_week(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a1", local_date="2025-06-02", distance_m=5000.0)
        _add_activity(conn, activity_id="a2", local_date="2025-06-04", distance_m=10000.0)
        conn.commit()
        for d in ("2025-06-02", "2025-06-04"):
            refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date=d)
        conn.commit()

        refresh_period_rollup(
            conn, athlete_id=DEFAULT_ATHLETE_ID, period_type="week", period_start="2025-06-02"
        )
        conn.commit()
        row = conn.execute(select(period_rollup)).fetchone()

    assert row is not None
    assert row.period_end == "2025-06-08"
    assert row.activity_count == 2
    assert row.activity_distance_m == 15000.0
    assert row.activity_days_count == 2


def test_health_metric_value_avg_is_weighted_not_naive_average_of_daily_averages(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _register_metric(conn, "steps")
        # Day 1: a single observation of 1000. Day 2: two observations averaging 4000 (sum
        # 8000). A naive average-of-daily-averages would give (1000 + 4000) / 2 = 2500; the
        # correct weighted average is (1000 + 8000) / 3 = 3000.
        _add_observation(conn, metric_key="steps", local_date="2025-06-02", value=1000.0, hour=6)
        _add_observation(conn, metric_key="steps", local_date="2025-06-03", value=3000.0, hour=6)
        _add_observation(conn, metric_key="steps", local_date="2025-06-03", value=5000.0, hour=20)
        conn.commit()
        for d in ("2025-06-02", "2025-06-03"):
            refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date=d)
        conn.commit()

        refresh_period_rollup(
            conn, athlete_id=DEFAULT_ATHLETE_ID, period_type="week", period_start="2025-06-02"
        )
        conn.commit()
        row = conn.execute(select(health_metric_period_rollup)).fetchone()

    assert row is not None
    assert row.value_sum == 9000.0
    assert row.n_observations == 3
    assert row.value_avg == 3000.0
    assert row.value_last == 5000.0  # from 2025-06-03, the later day with data


def test_refresh_is_idempotent_no_duplicate_rows(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a1", local_date="2025-06-02", distance_m=5000.0)
        conn.commit()
        refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date="2025-06-02")
        conn.commit()

        for _ in range(2):
            refresh_period_rollup(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                period_type="week",
                period_start="2025-06-02",
            )
            conn.commit()

        rows = conn.execute(select(period_rollup)).fetchall()

    assert len(rows) == 1


def test_retroactive_correction_to_an_old_day_is_reflected_in_its_week(tmp_path: Path) -> None:
    """Simulates a garmin_connect re-sync revising a day already inside a previously-refreshed
    week -- no watermark, just re-deriving touched_periods from touched_dates and refreshing
    again, same as a real ingest entry point would."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        _add_activity(conn, activity_id="a1", local_date="2025-06-02", distance_m=5000.0)
        conn.commit()
        refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date="2025-06-02")
        conn.commit()
        refresh_period_rollup(
            conn, athlete_id=DEFAULT_ATHLETE_ID, period_type="week", period_start="2025-06-02"
        )
        conn.commit()
        before = conn.execute(select(period_rollup)).fetchone()
        assert before is not None
        assert before.activity_distance_m == 5000.0

        # A "correction" -- a second activity discovered for the same already-rolled-up day.
        _add_activity(conn, activity_id="a2", local_date="2025-06-02", distance_m=2000.0)
        conn.commit()
        refresh_daily_rollup(conn, athlete_id=DEFAULT_ATHLETE_ID, local_date="2025-06-02")
        refresh_period_rollup(
            conn, athlete_id=DEFAULT_ATHLETE_ID, period_type="week", period_start="2025-06-02"
        )
        conn.commit()
        after = conn.execute(select(period_rollup)).fetchone()

    assert after is not None
    assert after.activity_distance_m == 7000.0
