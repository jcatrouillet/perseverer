"""Tests for GET /activities, GET /activities/{id}, GET /activities/{id}/stream."""

import datetime as dt
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from sporthealth.db.schema import activity_metric, activity_stream, metric_definition
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.fit.types import StreamPoint
from sporthealth.streams import write_activity_stream
from tests.api.conftest import seed_activity


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

    r = client.get("/api/v1/activities", headers=auth_headers)
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["avg_hr_bpm"] == 142.0
    assert item["max_hr_bpm"] == 171.0
    assert item["training_load"] == 81.8
    assert item["workout_rpe"] == 4.6

    r = client.get("/api/v1/activities/a1", headers=auth_headers)
    assert r.status_code == 200
    detail = r.json()
    assert detail["avg_hr_bpm"] == 142.0
    assert detail["training_load"] == 81.8
    assert detail["workout_rpe"] == 4.6


def test_list_activities_omits_hr_load_rpe_when_absent(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    r = client.get("/api/v1/activities", headers=auth_headers)
    item = r.json()["items"][0]
    assert item["avg_hr_bpm"] is None
    assert item["training_load"] is None
    assert item["workout_rpe"] is None


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
