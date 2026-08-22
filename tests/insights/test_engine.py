"""Engine orchestration tests: real DB assembly (activity + activity_metric + fitness rollup +
sleep_session) feeding into the pure rule modules, and the delete-and-reinsert refresh
contract."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    activity,
    activity_metric,
    athlete,
    fitness_daily_rollup,
    insight,
    metadata,
    metric_definition,
    sleep_session,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.insights.engine import refresh_insights


def _seed_athlete(engine) -> None:  # type: ignore[no-untyped-def]
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


def _seed_activity(engine, activity_id: str, local_date: str) -> None:  # type: ignore[no-untyped-def]
    now = dt.datetime.fromisoformat(local_date + "T08:00:00")
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date=local_date,
                sport="running",
                duration_s=1500.0,
                moving_duration_s=1500.0,
                distance_m=5000.0,
                elevation_gain_m=50.0,
                calories=300.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            metric_definition.insert().values(
                metric_key="fit.session.avg_heart_rate",
                display_name="fit.session.avg_heart_rate",
                category="activity",
                value_type="numeric",
                first_seen_at=now,
                first_seen_source="fit_folder",
            )
        )
        conn.execute(
            activity_metric.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                metric_key="fit.session.avg_heart_rate",
                value_num=140.0,
                source="fit_folder",
                created_at=now,
            )
        )
        conn.commit()


def _seed_activity_with_strava_alias_metrics(engine, activity_id: str, local_date: str) -> None:  # type: ignore[no-untyped-def]
    """A GPX/TCX-sourced Strava activity: no fit.session.* keys at all, only the
    strava.session.* aliases the CSV-totals overlay emits (ADR 0013)."""
    now = dt.datetime.fromisoformat(local_date + "T08:00:00")
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date=local_date,
                sport="running",
                duration_s=1500.0,
                moving_duration_s=1500.0,
                distance_m=5000.0,
                elevation_gain_m=50.0,
                calories=300.0,
                primary_source="strava_export",
                created_at=now,
                updated_at=now,
            )
        )
        for key, value in (
            ("strava.session.avg_heart_rate", 138.0),
            ("strava.session.max_heart_rate", 172.0),
            ("strava.session.total_descent", 42.0),
        ):
            conn.execute(
                metric_definition.insert().values(
                    metric_key=key,
                    display_name=key,
                    category="activity",
                    value_type="numeric",
                    first_seen_at=now,
                    first_seen_source="strava_export",
                )
            )
            conn.execute(
                activity_metric.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id=activity_id,
                    metric_key=key,
                    value_num=value,
                    source="strava_export",
                    created_at=now,
                )
            )
        conn.commit()


def test_strava_session_alias_metrics_feed_insight_activities(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    _seed_activity_with_strava_alias_metrics(engine, "a1", "2026-08-14")

    with engine.connect() as conn:
        count = refresh_insights(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2026, 8, 14))
        conn.commit()
        rows = conn.execute(
            select(insight).where(insight.c.athlete_id == DEFAULT_ATHLETE_ID)
        ).fetchall()

    assert count == len(rows)
    avg_hr_rows = [r for r in rows if r.subject_key == "avg_hr_high:run"]
    assert avg_hr_rows and avg_hr_rows[0].value_num == 138.0


def test_refresh_insights_writes_effort_and_streak_rows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    _seed_activity(engine, "a1", "2026-08-14")

    with engine.connect() as conn:
        count = refresh_insights(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2026, 8, 14))
        conn.commit()
        rows = conn.execute(
            select(insight).where(insight.c.athlete_id == DEFAULT_ATHLETE_ID)
        ).fetchall()

    assert count == len(rows)
    assert any(r.kind == "effort" and r.subject_key == "distance:run" for r in rows)
    avg_hr_rows = [r for r in rows if r.subject_key == "avg_hr_high:run"]
    assert avg_hr_rows and avg_hr_rows[0].value_num == 140.0


def test_refresh_insights_is_a_clean_delete_and_reinsert(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    _seed_activity(engine, "a1", "2026-08-14")

    with engine.connect() as conn:
        refresh_insights(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2026, 8, 14))
        conn.commit()
        first_count = conn.execute(
            select(insight).where(insight.c.athlete_id == DEFAULT_ATHLETE_ID)
        ).fetchall()

        refresh_insights(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=dt.date(2026, 8, 14))
        conn.commit()
        second_count = conn.execute(
            select(insight).where(insight.c.athlete_id == DEFAULT_ATHLETE_ID)
        ).fetchall()

    assert len(first_count) == len(second_count)  # no duplication on re-run


def test_load_and_health_insights_are_assembled_from_real_tables(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    _seed_athlete(engine)
    as_of = dt.date(2026, 8, 14)

    with engine.connect() as conn:
        # A sustained-low-TSB streak ending today.
        for i in range(6):
            d = as_of - dt.timedelta(days=5 - i)
            conn.execute(
                fitness_daily_rollup.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    local_date=d.isoformat(),
                    training_load=50.0,
                    ctl=40.0,
                    atl=60.0,
                    tsb=-25.0,
                    refreshed_at=dt.datetime.now(dt.UTC),
                )
            )
        conn.execute(
            sleep_session.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date=as_of.isoformat(),
                start_time_utc=dt.datetime(2026, 8, 13, 23, 0),
                end_time_utc=dt.datetime(2026, 8, 14, 6, 0),
                total_sleep_s=25200.0,
                sleep_score=40.0,
                source="fit_folder",
            )
        )
        for i in range(1, 31):
            d = as_of - dt.timedelta(days=i)
            conn.execute(
                sleep_session.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    local_date=d.isoformat(),
                    start_time_utc=dt.datetime.combine(d, dt.time(23, 0)),
                    end_time_utc=dt.datetime.combine(
                        d + dt.timedelta(days=1), dt.time(6, 0)
                    ),
                    total_sleep_s=25200.0,
                    sleep_score=80.0,
                    source="fit_folder",
                )
            )
        conn.commit()

        refresh_insights(conn, athlete_id=DEFAULT_ATHLETE_ID, as_of=as_of)
        conn.commit()
        rows = conn.execute(
            select(insight).where(insight.c.athlete_id == DEFAULT_ATHLETE_ID)
        ).fetchall()

    assert any(r.subject_key == "load:sustained_low_tsb" for r in rows)
    assert any(r.subject_key == "health:sleep_score_drop" for r in rows)
