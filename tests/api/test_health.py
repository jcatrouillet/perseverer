"""Tests for GET /health/observations."""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import health_metric_daily_rollup, health_observation, metric_definition
from perseverer.db.seed import DEFAULT_ATHLETE_ID


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


def _seed_body_observation(
    engine: Engine,
    *,
    metric_key: str,
    local_date: str,
    value: float,
    observed_at_utc: dt.datetime | None = None,
    source: str = "eufy",
) -> None:
    """Like _seed_observation, but with a caller-controlled `observed_at_utc` -- needed to
    reproduce same-day-multiple-readings scenarios (a shared scale used by more than one
    person on the same day) for the body-composition outlier filter, which orders by
    observed_at_utc to pick each day's "last" reading."""
    now = dt.datetime.now(dt.UTC)
    observed = (observed_at_utc or now).replace(tzinfo=None)
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
                    first_seen_source=source,
                )
            )
        conn.execute(
            health_observation.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                metric_key=metric_key,
                observed_at_utc=observed,
                local_date=local_date,
                aggregation="instant",
                value_num=value,
                source=source,
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


def test_health_dashboard_drops_a_body_composition_outlier_reading(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # Mirrors a real anomaly found in production: a single reading far off from the athlete's
    # own trend (a different, much lighter person on the shared Eufy scale) must not appear in
    # the dashboard, while the surrounding real days stay untouched.
    weight_key = "eufy.scale.weight"
    _seed_body_observation(engine, metric_key=weight_key, local_date="2026-08-08", value=79.5)
    _seed_body_observation(engine, metric_key=weight_key, local_date="2026-08-09", value=79.0)
    _seed_body_observation(engine, metric_key=weight_key, local_date="2026-08-11", value=51.9)
    _seed_body_observation(engine, metric_key=weight_key, local_date="2026-08-12", value=79.2)

    r = client.get(
        "/api/v1/health/dashboard?start_date=2026-08-01&end_date=2026-08-31",
        headers=auth_headers,
    )
    assert r.status_code == 200
    metrics = {m["logical_metric"]: m for m in r.json()["metrics"]}
    dates = {d["local_date"] for d in metrics["weight_kg"]["daily"]}
    assert dates == {"2026-08-08", "2026-08-09", "2026-08-12"}


def test_health_dashboard_merges_apple_health_pre_eufy_weight_with_eufy(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # Apple Health's pre-Eufy era (apple_health.body_mass) and Eufy's own readings
    # (eufy.scale.weight) never share a date by construction (see
    # adapters/apple_health_export.py's weight_before cutoff) -- the dashboard's weight_kg
    # chart must still stitch both sources into one continuous chronological series, including
    # running the same sequential outlier rejection across the source boundary.
    _seed_body_observation(
        engine,
        metric_key="apple_health.body_mass",
        local_date="2015-06-01",
        value=84.0,
        observed_at_utc=dt.datetime(2015, 6, 1, 8, tzinfo=dt.UTC),
        source="apple_health_export",
    )
    _seed_body_observation(
        engine,
        metric_key="apple_health.body_mass",
        local_date="2020-09-25",
        value=83.9,
        observed_at_utc=dt.datetime(2020, 9, 25, 8, tzinfo=dt.UTC),
        source="apple_health_export",
    )
    _seed_body_observation(
        engine,
        metric_key="eufy.scale.weight",
        local_date="2020-11-12",
        value=83.8,
        observed_at_utc=dt.datetime(2020, 11, 12, 2, tzinfo=dt.UTC),
    )

    r = client.get(
        "/api/v1/health/dashboard?start_date=2015-01-01&end_date=2021-01-01",
        headers=auth_headers,
    )
    assert r.status_code == 200
    metrics = {m["logical_metric"]: m for m in r.json()["metrics"]}
    daily = {d["local_date"]: d for d in metrics["weight_kg"]["daily"]}
    assert daily.keys() == {"2015-06-01", "2020-09-25", "2020-11-12"}
    assert daily["2015-06-01"]["source_metric_key"] == "apple_health.body_mass"
    assert daily["2020-09-25"]["source_metric_key"] == "apple_health.body_mass"
    assert daily["2020-11-12"]["source_metric_key"] == "eufy.scale.weight"
    assert daily["2020-11-12"]["value_last"] == 83.8


def test_health_dashboard_excludes_a_bad_reading_from_a_mixed_day_average(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # Mirrors a second real anomaly found in production (2025-09-21): a shared scale produced
    # three raw readings in one day, one real (~81kg) and two duplicate readings from someone
    # else (~48kg) -- a naive daily average across all three would already be contaminated
    # before any day-level check ever ran. The bad readings must be excluded from the day's
    # aggregate, not just flagged as a whole-day outlier. Every row gets an explicit
    # observed_at_utc (not the helper's default of "now") so the sequential baseline walk
    # replays them in the intended chronological order regardless of when this test runs.
    weight_key = "eufy.scale.weight"
    for i, d in enumerate(("2025-09-14", "2025-09-15", "2025-09-16", "2025-09-17", "2025-09-18")):
        _seed_body_observation(
            engine,
            metric_key=weight_key,
            local_date=d,
            value=79.0,
            observed_at_utc=dt.datetime(2025, 9, 14 + i, 7, tzinfo=dt.UTC),
        )
    _seed_body_observation(
        engine,
        metric_key=weight_key,
        local_date="2025-09-21",
        value=81.3,
        observed_at_utc=dt.datetime(2025, 9, 21, 7, tzinfo=dt.UTC),
    )
    _seed_body_observation(
        engine,
        metric_key=weight_key,
        local_date="2025-09-21",
        value=48.1,
        observed_at_utc=dt.datetime(2025, 9, 21, 8, tzinfo=dt.UTC),
    )
    _seed_body_observation(
        engine,
        metric_key=weight_key,
        local_date="2025-09-21",
        value=48.1,
        observed_at_utc=dt.datetime(2025, 9, 21, 9, tzinfo=dt.UTC),
    )

    r = client.get(
        "/api/v1/health/dashboard?start_date=2025-09-01&end_date=2025-09-30",
        headers=auth_headers,
    )
    assert r.status_code == 200
    metrics = {m["logical_metric"]: m for m in r.json()["metrics"]}
    day = next(d for d in metrics["weight_kg"]["daily"] if d["local_date"] == "2025-09-21")
    assert day["value_avg"] == 81.3
    assert day["value_last"] == 81.3
    assert day["n_observations"] == 1


def test_health_dashboard_rejects_a_whole_run_of_bad_readings_that_outnumber_the_real_ones(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # Mirrors a third real anomaly found in production: a multi-week stretch where the other
    # person's readings actually outnumber the athlete's own -- a symmetric neighbor-window
    # comparison (even a wide one) gets pulled toward the wrong cluster here, since it's the
    # local majority. The sequential baseline must reject all of them anyway, because they're
    # compared to the last *accepted* value, never to each other.
    weight_key = "eufy.scale.weight"
    _seed_body_observation(
        engine,
        metric_key=weight_key,
        local_date="2025-10-01",
        value=80.0,
        observed_at_utc=dt.datetime(2025, 10, 1, 7, tzinfo=dt.UTC),
    )
    bad_dates = ["2025-10-05", "2025-10-08", "2025-10-12", "2025-10-15", "2025-10-19"]
    for i, d in enumerate(bad_dates):
        _seed_body_observation(
            engine,
            metric_key=weight_key,
            local_date=d,
            value=46.0 + i,  # a slight drift, still nowhere near the real baseline
            observed_at_utc=dt.datetime.combine(
                dt.date.fromisoformat(d), dt.time(7, tzinfo=dt.UTC)
            ),
        )
    _seed_body_observation(
        engine,
        metric_key=weight_key,
        local_date="2025-10-22",
        value=81.0,
        observed_at_utc=dt.datetime(2025, 10, 22, 7, tzinfo=dt.UTC),
    )

    r = client.get(
        "/api/v1/health/dashboard?start_date=2025-10-01&end_date=2025-10-31",
        headers=auth_headers,
    )
    assert r.status_code == 200
    metrics = {m["logical_metric"]: m for m in r.json()["metrics"]}
    dates = {d["local_date"] for d in metrics["weight_kg"]["daily"]}
    assert dates == {"2025-10-01", "2025-10-22"}


def test_health_dashboard_accepts_the_first_ever_reading_with_nothing_to_compare_against(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # The very first reading for a metric has no prior baseline to be judged against, so it must
    # be shown rather than silently hidden.
    _seed_body_observation(
        engine, metric_key="eufy.scale.weight", local_date="2025-09-16", value=46.1
    )

    r = client.get(
        "/api/v1/health/dashboard?start_date=2025-09-01&end_date=2025-09-30",
        headers=auth_headers,
    )
    assert r.status_code == 200
    metrics = {m["logical_metric"]: m for m in r.json()["metrics"]}
    dates = {d["local_date"] for d in metrics["weight_kg"]["daily"]}
    assert dates == {"2025-09-16"}


def test_health_dashboard_does_not_filter_non_body_composition_outliers(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # The outlier filter is deliberately scoped to Eufy body-composition metrics -- a genuine
    # spike in e.g. steps or resting heart rate is real data, not a shared-device artifact, and
    # must never be silently dropped.
    steps_key = "garmin.daily_summary.totalSteps"
    _seed_rollup(engine, metric_key=steps_key, local_date="2026-08-08", value=5000.0)
    _seed_rollup(engine, metric_key=steps_key, local_date="2026-08-09", value=5200.0)
    _seed_rollup(engine, metric_key=steps_key, local_date="2026-08-11", value=25000.0)
    _seed_rollup(engine, metric_key=steps_key, local_date="2026-08-12", value=5100.0)

    r = client.get(
        "/api/v1/health/dashboard?start_date=2026-08-01&end_date=2026-08-31",
        headers=auth_headers,
    )
    assert r.status_code == 200
    metrics = {m["logical_metric"]: m for m in r.json()["metrics"]}
    dates = {d["local_date"] for d in metrics["steps"]["daily"]}
    assert dates == {"2026-08-08", "2026-08-09", "2026-08-11", "2026-08-12"}
