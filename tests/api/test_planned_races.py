"""Tests for GET/POST/PUT/DELETE /planned-races -- the router's own responsibilities (id-keyed
CRUD, the by-date and date-range list routes, request validation) and the two computed
read-only fields (days_until, predicted_duration_s). planned_races.py's own prediction-lookup
logic is covered directly in tests/test_planned_races.py.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import performance_daily_rollup
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def _create(
    client: TestClient, auth_headers: dict[str, str], local_date: str, **fields: Any
) -> dict[str, Any]:
    body = {"local_date": local_date, "name": "Test race", "distance_m": 10000.0, **fields}
    r = client.post("/api/v1/planned-races", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_get_by_id_404s_for_nonexistent_race(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/planned-races/999999", headers=auth_headers)
    assert r.status_code == 404


def test_by_date_list_is_empty_when_no_race_scheduled(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/planned-races/by-date/2026-09-01", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_post_creates_with_defaults_and_computes_days_until(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    target_date = (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=30)).isoformat()
    body = _create(
        client,
        auth_headers,
        target_date,
        name="Paris Marathon",
        distance_m=42195.0,
        scheduled_time="09:00",
        target_duration_s=14340.0,
    )
    assert body["name"] == "Paris Marathon"
    assert body["sport"] == "running"  # default
    assert body["distance_m"] == 42195.0
    assert body["scheduled_time"] == "09:00"
    assert body["target_duration_s"] == 14340.0
    assert body["days_until"] == 30
    assert body["predicted_duration_s"] is None  # no performance rollup seeded

    get = client.get(f"/api/v1/planned-races/{body['id']}", headers=auth_headers)
    assert get.status_code == 200
    assert get.json()["name"] == "Paris Marathon"


def test_post_accepts_a_non_running_sport(client: TestClient, auth_headers: dict[str, str]) -> None:
    body = _create(
        client, auth_headers, "2026-09-01", sport="cycling", name="Gran Fondo", distance_m=100000.0
    )
    assert body["sport"] == "cycling"


def test_post_defaults_scheduled_time_and_target_to_null(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _create(client, auth_headers, "2026-09-01")
    assert body["scheduled_time"] is None
    assert body["target_duration_s"] is None


def test_post_rejects_a_blank_name(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.post(
        "/api/v1/planned-races",
        json={"local_date": "2026-09-01", "name": "  ", "distance_m": 5000.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_post_rejects_a_non_positive_distance(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/api/v1/planned-races",
        json={"local_date": "2026-09-01", "name": "Race", "distance_m": 0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_post_rejects_a_malformed_scheduled_time(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/api/v1/planned-races",
        json={
            "local_date": "2026-09-01",
            "name": "Race",
            "distance_m": 5000.0,
            "scheduled_time": "9am",
        },
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_post_rejects_an_invalid_date(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.post(
        "/api/v1/planned-races",
        json={"local_date": "not-a-date", "name": "Race", "distance_m": 5000.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_predicted_duration_s_reflects_a_seeded_performance_rollup(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        conn.execute(
            performance_daily_rollup.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-01",
                predicted_10k_s=2380.0,
                refreshed_at=dt.datetime(2026, 9, 1),
            )
        )
        conn.commit()

    body = _create(client, auth_headers, "2026-10-01", name="10K", distance_m=10000.0)
    assert body["predicted_duration_s"] == 2380.0


def test_by_date_and_range_list_return_the_same_race(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(client, auth_headers, "2026-09-15", name="Half Marathon", distance_m=21097.5)

    by_date = client.get("/api/v1/planned-races/by-date/2026-09-15", headers=auth_headers)
    assert by_date.status_code == 200
    assert [r["id"] for r in by_date.json()] == [created["id"]]

    ranged = client.get(
        "/api/v1/planned-races?start_date=2026-09-01&end_date=2026-09-30", headers=auth_headers
    )
    assert ranged.status_code == 200
    assert [r["id"] for r in ranged.json()] == [created["id"]]

    outside = client.get(
        "/api/v1/planned-races?start_date=2026-10-01&end_date=2026-10-31", headers=auth_headers
    )
    assert outside.json() == []


def test_put_updates_in_place(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = _create(client, auth_headers, "2026-09-01", name="Draft race", distance_m=5000.0)

    r = client.put(
        f"/api/v1/planned-races/{created['id']}",
        json={
            "local_date": "2026-09-02",
            "name": "Final name",
            "distance_m": 10000.0,
            "target_duration_s": 2700.0,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["id"]
    assert body["local_date"] == "2026-09-02"
    assert body["name"] == "Final name"
    assert body["distance_m"] == 10000.0
    assert body["target_duration_s"] == 2700.0


def test_put_404s_for_nonexistent_race(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.put(
        "/api/v1/planned-races/999999",
        json={"local_date": "2026-09-01", "name": "Race", "distance_m": 5000.0},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_delete_removes_the_race(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = _create(client, auth_headers, "2026-09-01")
    r = client.delete(f"/api/v1/planned-races/{created['id']}", headers=auth_headers)
    assert r.status_code == 200
    after = client.get(f"/api/v1/planned-races/{created['id']}", headers=auth_headers)
    assert after.status_code == 404


def test_delete_404s_for_nonexistent_race(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.delete("/api/v1/planned-races/999999", headers=auth_headers)
    assert r.status_code == 404


def test_endpoints_require_auth(client: TestClient) -> None:
    listed = client.get("/api/v1/planned-races?start_date=2026-01-01&end_date=2026-12-31")
    assert listed.status_code in (401, 403)
    assert client.get("/api/v1/planned-races/by-date/2026-09-01").status_code in (401, 403)
    assert client.get("/api/v1/planned-races/1").status_code in (401, 403)
    assert client.post("/api/v1/planned-races", json={}).status_code in (401, 403)
    assert client.put("/api/v1/planned-races/1", json={}).status_code in (401, 403)
    assert client.delete("/api/v1/planned-races/1").status_code in (401, 403)
