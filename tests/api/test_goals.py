"""Tests for GET/PUT/DELETE /goals -- goals.py's own compute_progress math is exhaustively
covered by tests/test_goals.py; these check the router's own responsibilities: the
available=False no-goal-set case, upsert-not-duplicate, validation, and auth."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.db.schema import goal as goal_table
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
        json={"period_type": "day", "period_start": "2026-01-01", "target_distance_m": 100.0},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_a_week_running_goal_can_be_created_read_and_deleted(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(
            conn, activity_id="w1", sport="running", local_date="2026-09-15", distance_m=12_000.0
        )
    put = client.put(
        "/api/v1/goals",
        json={
            "period_type": "week",
            "period_start": "2026-09-14",
            "sport": "running",
            "target_distance_m": 40_000.0,
        },
        headers=auth_headers,
    )
    assert put.status_code == 200
    assert put.json()["period_type"] == "week"

    got = client.get(
        "/api/v1/goals",
        params={"period_type": "week", "period_start": "2026-09-14"},
        headers=auth_headers,
    ).json()
    assert got["available"] is True
    assert got["period_end"] == "2026-09-20"
    assert got["current_distance_m"] == 12_000.0
    # A different week has its own (absent) goal.
    other = client.get(
        "/api/v1/goals",
        params={"period_type": "week", "period_start": "2026-09-21"},
        headers=auth_headers,
    ).json()
    assert other["available"] is False

    bad = client.put(
        "/api/v1/goals",
        json={"period_type": "week", "period_start": "2026", "target_distance_m": 1.0},
        headers=auth_headers,
    )
    assert bad.status_code == 422

    assert (
        client.delete(f"/api/v1/goals/{put.json()['id']}", headers=auth_headers).status_code == 200
    )


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


def _repeat(client: TestClient, headers: dict[str, str], **overrides: object) -> httpx.Response:
    body: dict[str, object] = {
        "period_type": "week",
        "period_start": "2026-09-28",
        "sport": "running",
        "target_distance_m": 40_000.0,
        "weeks": 4,
    }
    body.update(overrides)
    response: httpx.Response = client.post("/api/v1/goals/repeat", json=body, headers=headers)
    return response


def test_repeat_creates_the_same_weekly_goal_for_consecutive_weeks(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = _repeat(client, auth_headers)
    assert r.status_code == 200
    starts = [g["period_start"] for g in r.json()["goals"]]
    assert starts == ["2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"]
    assert {g["target_distance_m"] for g in r.json()["goals"]} == {40_000.0}

    for start in starts:
        got = client.get(
            "/api/v1/goals",
            params={"period_type": "week", "period_start": start},
            headers=auth_headers,
        ).json()
        assert got["available"] is True
    after = client.get(
        "/api/v1/goals",
        params={"period_type": "week", "period_start": "2026-10-26"},
        headers=auth_headers,
    ).json()
    assert after["available"] is False


def test_repeat_replaces_an_existing_goal_in_a_covered_week(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    existing = client.put(
        "/api/v1/goals",
        json={
            "period_type": "week",
            "period_start": "2026-10-05",
            "sport": "running",
            "target_distance_m": 10_000.0,
        },
        headers=auth_headers,
    ).json()
    r = _repeat(client, auth_headers, weeks=2)
    by_start = {g["period_start"]: g for g in r.json()["goals"]}
    # Same row updated in place (upsert), not a second goal for that week.
    assert by_start["2026-10-05"]["id"] == existing["id"]
    assert by_start["2026-10-05"]["target_distance_m"] == 40_000.0


def test_repeat_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    def status(**overrides: object) -> int:
        return int(_repeat(client, auth_headers, **overrides).status_code)

    assert status(weeks=0) == 422
    assert status(weeks=105) == 422
    assert status(period_type="month", period_start="2026-10") == 422
    assert status(period_start="not-a-date") == 422
    assert status(weeks=1) == 200
