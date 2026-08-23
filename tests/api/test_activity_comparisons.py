"""Tests for GET /activities/{id}/comparisons -- same-distance, same-start-location comparison
runs. Modeled on test_activity_context.py, the closest existing analog (same "deliberately small,
honest comparison view" endpoint shape, same seed_activity/conftest fixtures)."""

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import activity_metric, metric_definition, route_geom
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from tests.api.conftest import seed_activity

# A real starting point (roughly central Paris) -- the exact coordinates don't matter, only the
# offsets between them relative to _COMPARISON_START_RADIUS_M (300m).
HOME_LAT, HOME_LNG = 48.8566, 2.3522


def _add_route(
    engine: Engine, *, activity_id: str, start_lat: float | None, start_lng: float | None
) -> None:
    with engine.connect() as conn:
        conn.execute(
            route_geom.insert().values(
                activity_id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_lat=start_lat,
                start_lng=start_lng,
            )
        )
        conn.commit()


def _add_metric(engine: Engine, *, activity_id: str, metric_key: str, value: float) -> None:
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        conn.execute(
            metric_definition.insert().values(
                metric_key=metric_key,
                display_name=metric_key,
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
                metric_key=metric_key,
                value_num=value,
                source="fit_folder",
                created_at=now,
            )
        )
        conn.commit()


def test_404_for_unknown_activity(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get("/api/v1/activities/doesnotexist/comparisons", headers=auth_headers)
    assert r.status_code == 404


def test_empty_when_target_has_no_recorded_start_point(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """A treadmill run (or any activity with no GPS route) has nothing to match a start
    location against -- an honest empty result, not an error."""
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="running", distance_m=5000.0)

    r = client.get("/api/v1/activities/a1/comparisons", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == []
    assert body["matched_count"] == 0


def test_matches_same_distance_and_nearby_start_excludes_self(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running", distance_m=5000.0)
        seed_activity(
            conn, activity_id="nearby", sport="running", local_date="2025-05-01",
            distance_m=5200.0,
        )
    _add_route(engine, activity_id="target", start_lat=HOME_LAT, start_lng=HOME_LNG)
    # ~50m away -- well within the 300m radius.
    _add_route(engine, activity_id="nearby", start_lat=HOME_LAT + 0.00045, start_lng=HOME_LNG)

    r = client.get("/api/v1/activities/target/comparisons", headers=auth_headers)
    body = r.json()
    assert body["matched_count"] == 1
    assert [row["id"] for row in body["rows"]] == ["nearby"]


def test_excludes_a_start_point_far_away(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running", distance_m=5000.0)
        seed_activity(
            conn, activity_id="far", sport="running", local_date="2025-05-01", distance_m=5000.0,
        )
    _add_route(engine, activity_id="target", start_lat=HOME_LAT, start_lng=HOME_LNG)
    # Roughly 1.1km away (0.01 degrees latitude) -- well outside the 300m radius.
    _add_route(engine, activity_id="far", start_lat=HOME_LAT + 0.01, start_lng=HOME_LNG)

    r = client.get("/api/v1/activities/target/comparisons", headers=auth_headers)
    body = r.json()
    assert body["matched_count"] == 0
    assert body["rows"] == []


def test_excludes_activities_outside_the_15_percent_distance_band(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running", distance_m=5000.0)
        seed_activity(
            conn, activity_id="too_far", sport="running", local_date="2025-05-01",
            distance_m=10000.0,
        )
    _add_route(engine, activity_id="target", start_lat=HOME_LAT, start_lng=HOME_LNG)
    _add_route(engine, activity_id="too_far", start_lat=HOME_LAT, start_lng=HOME_LNG)

    r = client.get("/api/v1/activities/target/comparisons", headers=auth_headers)
    body = r.json()
    assert body["matched_count"] == 0


def test_excludes_a_different_sport_at_the_same_location_and_distance(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running", distance_m=5000.0)
        seed_activity(
            conn, activity_id="bike", sport="cycling", local_date="2025-05-01",
            distance_m=5000.0,
        )
    _add_route(engine, activity_id="target", start_lat=HOME_LAT, start_lng=HOME_LNG)
    _add_route(engine, activity_id="bike", start_lat=HOME_LAT, start_lng=HOME_LNG)

    r = client.get("/api/v1/activities/target/comparisons", headers=auth_headers)
    body = r.json()
    assert body["matched_count"] == 0


def test_sorted_most_recent_first_and_capped_at_10(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running", distance_m=5000.0)
        for i in range(12):
            seed_activity(
                conn, activity_id=f"r{i}", sport="running",
                local_date=f"2025-01-{i + 1:02d}", distance_m=5000.0,
            )
    _add_route(engine, activity_id="target", start_lat=HOME_LAT, start_lng=HOME_LNG)
    for i in range(12):
        _add_route(engine, activity_id=f"r{i}", start_lat=HOME_LAT, start_lng=HOME_LNG)

    r = client.get("/api/v1/activities/target/comparisons", headers=auth_headers)
    body = r.json()
    assert body["matched_count"] == 12
    assert len(body["rows"]) == 10
    # Most recent local_date (2025-01-12) first, descending.
    assert [row["id"] for row in body["rows"]] == [f"r{i}" for i in range(11, 1, -1)]


def test_rows_carry_vdot_gap_hr_and_doubled_cadence(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="target", sport="running", distance_m=5000.0)
        seed_activity(
            conn, activity_id="match", sport="running", local_date="2025-05-01",
            distance_m=5000.0,
        )
    _add_route(engine, activity_id="target", start_lat=HOME_LAT, start_lng=HOME_LNG)
    _add_route(engine, activity_id="match", start_lat=HOME_LAT, start_lng=HOME_LNG)
    _add_metric(engine, activity_id="match", metric_key="perseverer.performance.vdot", value=48.5)
    _add_metric(
        engine, activity_id="match",
        metric_key="perseverer.performance.avg_gap_speed_mps", value=3.2,
    )
    _add_metric(engine, activity_id="match", metric_key="fit.session.avg_heart_rate", value=151.0)
    # Raw single-foot rate -- the endpoint must double it to strides/min.
    _add_metric(
        engine, activity_id="match", metric_key="fit.session.avg_running_cadence", value=82.0
    )

    r = client.get("/api/v1/activities/target/comparisons", headers=auth_headers)
    row = r.json()["rows"][0]
    assert row["id"] == "match"
    assert row["vdot"] == 48.5
    assert row["avg_gap_speed_mps"] == 3.2
    assert row["avg_hr_bpm"] == 151.0
    assert row["avg_cadence_spm"] == 164.0


def test_echoes_the_thresholds_used(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="running", distance_m=5000.0)

    r = client.get("/api/v1/activities/a1/comparisons", headers=auth_headers)
    body = r.json()
    assert body["start_radius_m"] == 300.0
    assert body["distance_band_fraction"] == 0.15
