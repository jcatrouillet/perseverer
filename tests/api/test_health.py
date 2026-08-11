"""Tests for GET /health/observations."""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from sporthealth.db.schema import health_metric_daily_rollup, health_observation, metric_definition
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


def _seed_rollup(
    engine: Engine, *, metric_key: str, local_date: str, value: float, n: int = 1
) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
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
            health_metric_daily_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date=local_date,
                metric_key=metric_key,
                value_sum=value * n,
                value_avg=value,
                value_min=value,
                value_max=value,
                value_last=value,
                n_observations=n,
                refreshed_at=now,
            )
        )
        conn.commit()


def test_health_dashboard_merges_aliases_falling_back_to_second_when_first_has_no_data(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # Only the export-era alias has data for this day -- the merge must still surface it.
    _seed_rollup(
        engine, metric_key="garmin.export.UDSFile.totalSteps", local_date="2025-06-01", value=8000.0
    )

    r = client.get(
        "/api/v1/health/dashboard?start_date=2025-06-01&end_date=2025-06-30",
        headers=auth_headers,
    )
    assert r.status_code == 200
    metrics = {m["logical_metric"]: m for m in r.json()["metrics"]}
    assert "steps" in metrics
    day = metrics["steps"]["daily"][0]
    assert day["value_last"] == 8000.0
    assert day["source_metric_key"] == "garmin.export.UDSFile.totalSteps"


def test_health_dashboard_prefers_first_alias_when_both_have_data(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_rollup(
        engine, metric_key="garmin.daily_summary.totalSteps", local_date="2025-06-01", value=9000.0
    )
    _seed_rollup(
        engine, metric_key="garmin.export.UDSFile.totalSteps", local_date="2025-06-01", value=1.0
    )

    r = client.get(
        "/api/v1/health/dashboard?start_date=2025-06-01&end_date=2025-06-30",
        headers=auth_headers,
    )
    metrics = {m["logical_metric"]: m for m in r.json()["metrics"]}
    day = metrics["steps"]["daily"][0]
    assert day["value_last"] == 9000.0
    assert day["source_metric_key"] == "garmin.daily_summary.totalSteps"


def test_health_dashboard_last_observed_is_not_bounded_by_the_requested_range(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_rollup(
        engine, metric_key="garmin.daily_summary.totalSteps", local_date="2025-01-15", value=5000.0
    )

    r = client.get(
        "/api/v1/health/dashboard?start_date=2025-06-01&end_date=2025-06-30",
        headers=auth_headers,
    )
    metrics = {m["logical_metric"]: m for m in r.json()["metrics"]}
    assert metrics["steps"]["daily"] == []  # nothing in the requested range
    assert metrics["steps"]["last_observed"] == "2025-01-15"  # but freshness is still reported


def test_health_dashboard_omits_logical_metrics_with_no_data_at_all(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _seed_rollup(
        engine, metric_key="garmin.daily_summary.totalSteps", local_date="2025-06-01", value=5000.0
    )

    r = client.get(
        "/api/v1/health/dashboard?start_date=2025-06-01&end_date=2025-06-30",
        headers=auth_headers,
    )
    logical_metrics = {m["logical_metric"] for m in r.json()["metrics"]}
    assert logical_metrics == {"steps"}


def test_health_dashboard_keeps_waking_and_sleep_respiration_distinct(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # These are two real, different readings (see LOGICAL_METRICS' own comment) -- a day can
    # have one, the other, both, or neither, and they must never merge into a single value.
    _seed_rollup(
        engine,
        metric_key="garmin.daily_summary.avgWakingRespirationValue",
        local_date="2025-06-01",
        value=14.0,
    )
    _seed_rollup(
        engine,
        metric_key="garmin.export.sleepData.averageRespiration",
        local_date="2025-06-01",
        value=13.4,
    )

    r = client.get(
        "/api/v1/health/dashboard?start_date=2025-06-01&end_date=2025-06-30",
        headers=auth_headers,
    )
    assert r.status_code == 200
    metrics = {m["logical_metric"]: m for m in r.json()["metrics"]}
    assert metrics["waking_respiration_rate"]["daily"][0]["value_last"] == 14.0
    assert metrics["sleep_respiration_rate"]["daily"][0]["value_last"] == 13.4
