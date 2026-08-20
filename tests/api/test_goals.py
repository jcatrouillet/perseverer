"""Tests for GET/PUT/DELETE /goals -- goals.py's own compute_progress math is exhaustively
covered by tests/test_goals.py; these check the router's own responsibilities: the
available=False no-goal-set case, upsert-not-duplicate, validation, and auth."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from sporthealth.db.schema import goal as goal_table
from tests.api.conftest import seed_activity


def test_get_unavailable_when_no_goal_set(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get(
        "/api/v1/goals",
        params={"period_type": "year", "period_start": "2026"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["goal"] is None
    assert body["daily"] == []


def test_put_creates_a_goal_and_get_returns_progress(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1", sport="running", local_date="2026-06-01")

    put = client.put(
        "/api/v1/goals",
        json={
            "period_type": "year",
            "period_start": "2026",
            "sport": "running",
            "target_distance_m": 1_000_000.0,
        },
        headers=auth_headers,
    )
    assert put.status_code == 200
    goal_id = put.json()["id"]
    assert put.json()["sport"] == "running"

    get = client.get(
        "/api/v1/goals",
        params={"period_type": "year", "period_start": "2026"},
        headers=auth_headers,
    )
    assert get.status_code == 200
    body = get.json()
    assert body["available"] is True
    assert body["goal"]["id"] == goal_id
    assert body["goal"]["target_distance_m"] == 1_000_000.0
    assert body["period_end"] == "2026-12-31"
    assert len(body["daily"]) > 0


def test_put_upserts_rather_than_duplicating(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    client.put(
        "/api/v1/goals",
        json={
            "period_type": "year",
            "period_start": "2026",
            "sport": "running",
            "target_distance_m": 1_000_000.0,
        },
        headers=auth_headers,
    )
    client.put(
        "/api/v1/goals",
        json={
            "period_type": "year",
            "period_start": "2026",
            "sport": "cycling",
            "target_distance_m": 2_000_000.0,
        },
        headers=auth_headers,
    )

    with engine.connect() as conn:
        rows = conn.execute(select(goal_table)).fetchall()
    assert len(rows) == 1
    assert rows[0].sport == "cycling"
    assert rows[0].target_distance_m == 2_000_000.0


def test_put_rejects_invalid_period_type(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.put(
        "/api/v1/goals",
        json={"period_type": "week", "period_start": "2026", "target_distance_m": 100.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_put_rejects_non_positive_target(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.put(
        "/api/v1/goals",
        json={"period_type": "year", "period_start": "2026", "target_distance_m": 0.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_put_rejects_malformed_period_start(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.put(
        "/api/v1/goals",
        json={"period_type": "month", "period_start": "2026/03", "target_distance_m": 100.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_delete_removes_the_goal(client: TestClient, auth_headers: dict[str, str]) -> None:
    put = client.put(
        "/api/v1/goals",
        json={"period_type": "year", "period_start": "2026", "target_distance_m": 100.0},
        headers=auth_headers,
    )
    goal_id = put.json()["id"]

    delete = client.delete(f"/api/v1/goals/{goal_id}", headers=auth_headers)
    assert delete.status_code == 200

    get = client.get(
        "/api/v1/goals",
        params={"period_type": "year", "period_start": "2026"},
        headers=auth_headers,
    )
    assert get.json()["available"] is False


def test_delete_404s_for_unknown_goal(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.delete("/api/v1/goals/999999", headers=auth_headers)
    assert r.status_code == 404


def test_endpoints_require_auth(client: TestClient) -> None:
    assert client.get(
        "/api/v1/goals", params={"period_type": "year", "period_start": "2026"}
    ).status_code in (401, 403)
    assert client.put("/api/v1/goals", json={}).status_code in (401, 403)
    assert client.delete("/api/v1/goals/1").status_code in (401, 403)
