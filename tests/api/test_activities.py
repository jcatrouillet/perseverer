"""Tests for GET /activities, GET /activities/{id}, GET /activities/{id}/stream."""

import datetime as dt
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from sporthealth.db.schema import (
    activity_metric,
    activity_stream,
    health_observation,
    metric_definition,
    route_geom,
)
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.fit.types import StreamPoint
from sporthealth.streams import write_activity_stream
from tests.api.conftest import seed_activity

_SWEAT_LOSS_METRIC_KEY = "garmin.export.HydrationLogFile.estimatedSweatLossInML"


def _add_metric(engine: Engine, *, activity_id: str, metric_key: str, value: float) -> None:
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        # activity_metric.metric_key FKs to metric_definition -- real ingestion auto-registers
        # this via metrics/registry.py before ever writing a value; tests have to do the same.
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


def _add_health_observation(
    engine: Engine,
    *,
    observed_at_utc: datetime,
    value_num: float,
    metric_key: str = _SWEAT_LOSS_METRIC_KEY,
) -> None:
    now = dt.datetime.now(dt.UTC)
    with engine.connect() as conn:
        exists = conn.execute(
            metric_definition.select().where(metric_definition.c.metric_key == metric_key)
        ).fetchone()
        if exists is None:
            conn.execute(
                metric_definition.insert().values(
                    metric_key=metric_key,
                    display_name=metric_key,
                    category="health",
                    value_type="numeric",
                    first_seen_at=now,
                    first_seen_source="garmin_export",
                )
            )
        conn.execute(
            health_observation.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                metric_key=metric_key,
                observed_at_utc=observed_at_utc,
                local_date=observed_at_utc.date().isoformat(),
                aggregation="daily",
                value_num=value_num,
                source="garmin_export",
            )
        )
        conn.commit()


def test_activity_years_returns_distinct_years_descending(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", local_date="2023-06-01")
        seed_activity(conn, activity_id="a2", local_date="2023-11-20")  # same year as a1
        seed_activity(conn, activity_id="a3", local_date="2025-01-15")
        seed_activity(conn, activity_id="a4", local_date="2020-08-02")

    r = client.get("/api/v1/activities/years", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == [2025, 2023, 2020]


def test_activity_years_empty_when_no_activities(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/activities/years", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_list_activities_paginates_and_filters_by_sport(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="running", local_date="2025-06-01")
        seed_activity(conn, activity_id="a2", sport="cycling", local_date="2025-06-02")

    r = client.get("/api/v1/activities", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2

    r = client.get("/api/v1/activities?sport=cycling", headers=auth_headers)
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "a2"

    r = client.get("/api/v1/activities?limit=1&offset=1", headers=auth_headers)
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


def test_list_and_detail_surface_hr_load_and_descaled_rpe(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_metric(engine, activity_id="a1", metric_key="fit.session.avg_heart_rate", value=142.0)
    _add_metric(engine, activity_id="a1", metric_key="fit.session.max_heart_rate", value=171.0)
    _add_metric(
        engine, activity_id="a1", metric_key="fit.session.training_load_peak", value=81.8
    )
    # Raw FIT value is Borg CR10 x10 (see routers/activities.py's _workout_rpe_from_raw comment,
    # sourced from introspecting the installed garmin_fit_sdk's profile.py field 193) -- 46 raw
    # must come back as 4.6, not 46.
    _add_metric(engine, activity_id="a1", metric_key="fit.session.workout_rpe", value=46.0)
    _add_metric(engine, activity_id="a1", metric_key="fit.user_profile.weight", value=80.1)

    r = client.get("/api/v1/activities", headers=auth_headers)
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["avg_hr_bpm"] == 142.0
    assert item["max_hr_bpm"] == 171.0
    assert item["training_load"] == 81.8
    assert item["workout_rpe"] == 4.6
    assert item["weight_kg"] == 80.1

    r = client.get("/api/v1/activities/a1", headers=auth_headers)
    assert r.status_code == 200
    detail = r.json()
    assert detail["avg_hr_bpm"] == 142.0
    assert detail["training_load"] == 81.8
    assert detail["workout_rpe"] == 4.6
    assert detail["weight_kg"] == 80.1


def test_strava_session_hr_alias_is_read_when_fit_key_absent(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # GPX/TCX-sourced Strava activities have no fit.session.* metric at all -- avg/max HR must
    # still surface via the strava.session.* alias (ADR 0013), in both list and detail.
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_metric(engine, activity_id="a1", metric_key="strava.session.avg_heart_rate", value=130.0)
    _add_metric(engine, activity_id="a1", metric_key="strava.session.max_heart_rate", value=160.0)

    r = client.get("/api/v1/activities", headers=auth_headers)
    item = r.json()["items"][0]
    assert item["avg_hr_bpm"] == 130.0
    assert item["max_hr_bpm"] == 160.0

    r = client.get("/api/v1/activities/a1", headers=auth_headers)
    detail = r.json()
    assert detail["avg_hr_bpm"] == 130.0
    assert detail["max_hr_bpm"] == 160.0


def test_fit_session_hr_is_preferred_over_strava_alias_when_both_present(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_metric(engine, activity_id="a1", metric_key="fit.session.avg_heart_rate", value=142.0)
    _add_metric(engine, activity_id="a1", metric_key="strava.session.avg_heart_rate", value=999.0)

    r = client.get("/api/v1/activities/a1", headers=auth_headers)
    assert r.json()["avg_hr_bpm"] == 142.0


def test_list_activities_omits_hr_load_rpe_when_absent(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    r = client.get("/api/v1/activities", headers=auth_headers)
    item = r.json()["items"][0]
    assert item["avg_hr_bpm"] is None
    assert item["training_load"] is None
    assert item["weight_kg"] is None
    assert item["workout_rpe"] is None


def test_list_and_detail_surface_vdot(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_metric(engine, activity_id="a1", metric_key="sporthealth.performance.vdot", value=41.2)

    r = client.get("/api/v1/activities", headers=auth_headers)
    assert r.json()["items"][0]["vdot"] == 41.2

    r = client.get("/api/v1/activities/a1", headers=auth_headers)
    assert r.json()["vdot"] == 41.2


def test_list_activities_omits_vdot_when_absent(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    r = client.get("/api/v1/activities", headers=auth_headers)
    assert r.json()["items"][0]["vdot"] is None


def test_get_activity_detail_404_for_unknown_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/activities/doesnotexist", headers=auth_headers)
    assert r.status_code == 404


def test_get_activity_detail_returns_full_shape(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    r = client.get("/api/v1/activities/a1", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "a1"
    assert body["sport"] == "running"
    assert body["stream_available"] is False
    assert body["laps"] == []
    assert body["device"] is None
    assert body["route"] is None


def test_get_activity_detail_includes_nearby_sweat_loss_observation(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    # seed_activity's fixed start (2025-06-01 10:00:00) + its default 1800s duration ends at
    # 10:30:00 -- real archived activities each paired with a hydration-log entry landing about
    # a minute after their own end (see _estimated_sweat_loss_ml's docstring), so 53s after here.
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_health_observation(
        engine, observed_at_utc=datetime(2025, 6, 1, 10, 30, 53), value_num=1234.0
    )

    r = client.get("/api/v1/activities/a1", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["estimated_sweat_loss_ml"] == 1234.0


def test_get_activity_detail_sweat_loss_null_when_no_nearby_observation(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    r = client.get("/api/v1/activities/a1", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["estimated_sweat_loss_ml"] is None


def test_get_activity_detail_sweat_loss_ignores_observation_outside_match_window(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    # 2000s after the 10:30:00 end -- past the 1800s window, so this must not be attributed to
    # this activity (it's more plausibly a later, unrelated activity's own hydration entry).
    _add_health_observation(
        engine, observed_at_utc=datetime(2025, 6, 1, 11, 3, 20), value_num=999.0
    )

    r = client.get("/api/v1/activities/a1", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["estimated_sweat_loss_ml"] is None


def test_get_activity_detail_sweat_loss_picks_the_nearest_observation(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
    _add_health_observation(
        engine, observed_at_utc=datetime(2025, 6, 1, 10, 45, 0), value_num=999.0
    )
    _add_health_observation(
        engine, observed_at_utc=datetime(2025, 6, 1, 10, 31, 40), value_num=500.0
    )

    r = client.get("/api/v1/activities/a1", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["estimated_sweat_loss_ml"] == 500.0


def test_stream_endpoint_downsamples_and_404s_without_stream(
    client: TestClient, auth_headers: dict[str, str], engine: Engine, tmp_path: Path
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    # No activity_stream row yet -- must 404, not error.
    r = client.get("/api/v1/activities/a1/stream", headers=auth_headers)
    assert r.status_code == 404

    start = datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC)
    points = [
        StreamPoint(
            timestamp_utc=start + timedelta(seconds=i),
            values={"heart_rate": 100.0 + i},
        )
        for i in range(300)
    ]
    rel_path, n_samples, channels = write_activity_stream(
        tmp_path / "parquet", DEFAULT_ATHLETE_ID, "a1", points
    )

    with engine.connect() as conn:
        conn.execute(
            activity_stream.insert().values(
                activity_id="a1",
                athlete_id=DEFAULT_ATHLETE_ID,
                parquet_path=rel_path,
                n_samples=n_samples,
                channels=json.dumps(channels),
                sample_rate_hint=1.0,
            )
        )
        conn.commit()

    r = client.get("/api/v1/activities/a1/stream?tier=low", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "low"
    assert len(body["timestamps"]) <= 300
    assert "heart_rate" in body["series"]

    r = client.get(
        "/api/v1/activities/a1/stream?channels=not_a_channel", headers=auth_headers
    )
    assert r.status_code == 400


def _add_route(
    engine: Engine,
    *,
    activity_id: str,
    start_lat: float = 47.6,
    start_lng: float = -122.3,
    simplified_polyline: str | None = None,
) -> None:
    with engine.connect() as conn:
        conn.execute(
            route_geom.insert().values(
                activity_id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_lat=start_lat,
                start_lng=start_lng,
                end_lat=start_lat,
                end_lng=start_lng,
                min_lat=start_lat,
                min_lng=start_lng,
                max_lat=start_lat,
                max_lng=start_lng,
                simplified_polyline=simplified_polyline,
            )
        )
        conn.commit()


def test_activity_map_points_returns_only_gps_bearing_activities(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="running", local_date="2025-06-01")
        seed_activity(conn, activity_id="a2", sport="strength_training", local_date="2025-06-02")
    _add_route(engine, activity_id="a1", start_lat=47.6, start_lng=-122.3)
    # a2 has no route_geom row (e.g. an indoor activity) -- must not appear.

    r = client.get("/api/v1/activities/map", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == "a1"
    assert body[0]["start_lat"] == 47.6
    assert body[0]["start_lng"] == -122.3


def test_activity_map_points_filters_by_sport_and_date(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="run1", sport="running", local_date="2025-06-01")
        seed_activity(conn, activity_id="ride1", sport="cycling", local_date="2025-07-01")
    _add_route(engine, activity_id="run1")
    _add_route(engine, activity_id="ride1")

    r = client.get("/api/v1/activities/map?sport=cycling", headers=auth_headers)
    assert [p["id"] for p in r.json()] == ["ride1"]

    r = client.get(
        "/api/v1/activities/map?start_date=2025-06-15&end_date=2025-06-30", headers=auth_headers
    )
    assert r.json() == []

    r = client.get(
        "/api/v1/activities/map?start_date=2025-05-01&end_date=2025-06-30", headers=auth_headers
    )
    assert [p["id"] for p in r.json()] == ["run1"]


def test_activity_routes_returns_only_requested_gps_bearing_ids(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="running", local_date="2025-06-01")
        seed_activity(conn, activity_id="a2", sport="running", local_date="2025-06-02")
        seed_activity(conn, activity_id="a3", sport="strength_training", local_date="2025-06-03")
    _add_route(engine, activity_id="a1", simplified_polyline="abc123")
    _add_route(engine, activity_id="a2", simplified_polyline="def456")
    # a3 has no route_geom row (indoor activity) -- must not appear even if its id is requested.

    r = client.get("/api/v1/activities/routes?ids=a1,a3,not-an-activity", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body == [{"id": "a1", "simplified_polyline": "abc123"}]

    # a2 not requested -- must not be returned even though it has a route.
    r = client.get("/api/v1/activities/routes?ids=a1", headers=auth_headers)
    assert [p["id"] for p in r.json()] == ["a1"]


def test_activity_routes_empty_ids_returns_empty_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/activities/routes?ids=", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []
