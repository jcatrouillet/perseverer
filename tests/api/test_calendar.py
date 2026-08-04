"""Tests for GET /calendar -- reads day_rollup/health_metric_daily_rollup directly (seeded
here, isolating the API-layer test from rollup-computation correctness, which
tests/test_rollups.py already covers).
"""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine

from sporthealth.db.schema import day_rollup, health_metric_daily_rollup, metric_definition
from sporthealth.db.seed import DEFAULT_ATHLETE_ID


def _register_metric(conn: Connection, metric_key: str, now: dt.datetime) -> None:
    conn.execute(
        metric_definition.insert().values(
            metric_key=metric_key,
            display_name=metric_key,
            category="health",
            value_type="numeric",
            first_seen_at=now,
            first_seen_source="fit_folder",
        )
    )


def test_calendar_returns_day_and_health_rollups_in_range(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        _register_metric(conn, "resting_heart_rate", now)
        conn.execute(
            day_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-01",
                activity_count=2,
                activity_duration_s=5400.0,
                activity_moving_duration_s=5200.0,
                activity_distance_m=25000.0,
                activity_elevation_gain_m=150.0,
                activity_calories=1200.0,
                sleep_total_s=25000.0,
                sleep_score=80.0,
                refreshed_at=now,
            )
        )
        conn.execute(
            day_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-07-01",
                activity_count=0,
                refreshed_at=now,
            )
        )
        conn.execute(
            health_metric_daily_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2025-06-01",
                metric_key="resting_heart_rate",
                value_sum=98.0,
                value_avg=49.0,
                value_min=48.0,
                value_max=50.0,
                value_last=50.0,
                n_observations=2,
                refreshed_at=now,
            )
        )
        conn.commit()

    r = client.get(
        "/api/v1/calendar?start_date=2025-06-01&end_date=2025-06-30", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["days"]) == 1
    day = body["days"][0]
    assert day["local_date"] == "2025-06-01"
    assert day["activity_count"] == 2
    assert len(day["health_metrics"]) == 1
    assert day["health_metrics"][0]["metric_key"] == "resting_heart_rate"
    assert day["health_metrics"][0]["value_avg"] == 49.0


def test_calendar_filters_health_metrics_by_metric_keys(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            day_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID, local_date="2025-06-01", activity_count=0,
                refreshed_at=now,
            )
        )
        for metric_key in ("resting_heart_rate", "steps"):
            _register_metric(conn, metric_key, now)
            conn.execute(
                health_metric_daily_rollup.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    local_date="2025-06-01",
                    metric_key=metric_key,
                    value_sum=1.0,
                    value_avg=1.0,
                    value_min=1.0,
                    value_max=1.0,
                    value_last=1.0,
                    n_observations=1,
                    refreshed_at=now,
                )
            )
        conn.commit()

    r = client.get(
        "/api/v1/calendar?start_date=2025-06-01&end_date=2025-06-01"
        "&metric_keys=steps",
        headers=auth_headers,
    )
    body = r.json()
    assert len(body["days"][0]["health_metrics"]) == 1
    assert body["days"][0]["health_metrics"][0]["metric_key"] == "steps"
