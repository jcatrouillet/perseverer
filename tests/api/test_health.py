"""Tests for GET /health/observations."""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from sporthealth.db.schema import health_observation, metric_definition
from sporthealth.db.seed import DEFAULT_ATHLETE_ID


def _seed_observation(engine: Engine, *, metric_key: str, local_date: str, value: float) -> None:
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        existing = conn.execute(
            metric_definition.select().where(metric_definition.c.metric_key == metric_key)
        ).fetchone()
        if existing is None:
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
        conn.execute(
            health_observation.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                metric_key=metric_key,
                observed_at_utc=now.replace(tzinfo=None),
                local_date=local_date,
                aggregation="daily",
                value_num=value,
                source="fit_folder",
            )
        )
        conn.commit()


def test_list_health_observations_filters_by_metric_and_date(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_observation(engine, metric_key="resting_heart_rate", local_date="2025-06-01", value=48.0)
    _seed_observation(engine, metric_key="resting_heart_rate", local_date="2025-07-01", value=50.0)
    _seed_observation(engine, metric_key="steps", local_date="2025-06-01", value=8000.0)

    r = client.get(
        "/api/v1/health/observations"
        "?metric_key=resting_heart_rate&start_date=2025-06-01&end_date=2025-06-30",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["value_num"] == 48.0


def test_list_health_observations_requires_metric_key(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/api/v1/health/observations?start_date=2025-06-01&end_date=2025-06-30",
        headers=auth_headers,
    )
    assert r.status_code == 422
