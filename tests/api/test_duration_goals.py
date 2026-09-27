"""Tests for /duration-goals: several goals per period (one per sport), duplicate refusal,
weekly repeat, validation, progress and auth."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.api.conftest import seed_activity


def _goal(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "period_type": "week",
        "period_start": "2026-09-14",
        "sport": "yoga",
        "target_duration_s": 10_800.0,
    }
    body.update(overrides)
    return body


def _list(client: TestClient, headers: dict[str, str], **params: str) -> list[dict[str, object]]:
    query = {"period_type": "week", "period_start": "2026-09-14", **params}
    r = client.get("/api/v1/duration-goals", params=query, headers=headers)
    assert r.status_code == 200
    body: list[dict[str, object]] = r.json()
    return body


def test_empty_when_no_goal_is_set(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert _list(client, auth_headers) == []


def test_any_sport_and_all_sports_goals_share_a_period_with_their_own_progress(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="y",
            sport="yoga",
            local_date="2026-09-15",
            duration_s=3600.0,
            distance_m=None,
            moving_duration_s=3600.0,
        )
        seed_activity(
            conn,
            activity_id="r",
            sport="running",
            local_date="2026-09-16",
            duration_s=1800.0,
            moving_duration_s=1800.0,
        )
    for body in (
        _goal(sport="yoga"),
        _goal(sport="strength_training", target_duration_s=7200.0),
        _goal(sport=None, target_duration_s=36_000.0),
    ):
        assert (
            client.post("/api/v1/duration-goals", json=body, headers=auth_headers).status_code
            == 201
        )

    goals = _list(client, auth_headers)
    by_sport = {g["goal"]["sport"]: g for g in goals}  # type: ignore[index]
    assert by_sport["yoga"]["current_duration_s"] == 3600
    assert by_sport["strength_training"]["current_duration_s"] == 0
    assert by_sport[None]["current_duration_s"] == 5400
    # A specific sport each, alphabetical, the every-sport goal last.
    assert [g["goal"]["sport"] for g in goals] == ["strength_training", "yoga", None]  # type: ignore[index]


def test_a_second_goal_for_the_same_period_and_sport_is_refused(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert (
        client.post("/api/v1/duration-goals", json=_goal(), headers=auth_headers).status_code == 201
    )
    dup = client.post(
        "/api/v1/duration-goals", json=_goal(target_duration_s=1.0), headers=auth_headers
    )
    assert dup.status_code == 409
    every = _goal(sport=None)
    assert (
        client.post("/api/v1/duration-goals", json=every, headers=auth_headers).status_code == 201
    )
    assert (
        client.post("/api/v1/duration-goals", json=every, headers=auth_headers).status_code == 409
    )
    other_week = _goal(period_start="2026-09-21")
    assert (
        client.post("/api/v1/duration-goals", json=other_week, headers=auth_headers).status_code
        == 201
    )


def test_update_and_delete(client: TestClient, auth_headers: dict[str, str]) -> None:
    a = client.post("/api/v1/duration-goals", json=_goal(), headers=auth_headers).json()
    client.post("/api/v1/duration-goals", json=_goal(sport="hiit"), headers=auth_headers)
    ok = client.put(
        f"/api/v1/duration-goals/{a['id']}",
        json=_goal(target_duration_s=5400.0),
        headers=auth_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["target_duration_s"] == 5400.0
    collide = client.put(
        f"/api/v1/duration-goals/{a['id']}", json=_goal(sport="hiit"), headers=auth_headers
    )
    assert collide.status_code == 409
    assert (
        client.put("/api/v1/duration-goals/9999", json=_goal(), headers=auth_headers).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/duration-goals/{a['id']}", headers=auth_headers).status_code == 200
    )
    assert (
        client.delete(f"/api/v1/duration-goals/{a['id']}", headers=auth_headers).status_code == 404
    )


def test_repeat_creates_consecutive_weeks_and_skips_covered_ones(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    client.post(
        "/api/v1/duration-goals", json=_goal(period_start="2026-09-28"), headers=auth_headers
    )
    r = client.post(
        "/api/v1/duration-goals/repeat", json={**_goal(), "weeks": 4}, headers=auth_headers
    )
    assert r.status_code == 201
    body = r.json()
    assert [g["period_start"] for g in body["created"]] == [
        "2026-09-14",
        "2026-09-21",
        "2026-10-05",
    ]
    assert body["skipped_period_starts"] == ["2026-09-28"]


def test_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    def post(path: str = "", **overrides: object) -> int:
        r = client.post(
            f"/api/v1/duration-goals{path}", json=_goal(**overrides), headers=auth_headers
        )
        return int(r.status_code)

    assert post(target_duration_s=0) == 422
    assert post(period_type="day") == 422
    assert post(period_type="month", period_start="2026-13") == 422
    assert post(period_start="not-a-date") == 422
    assert post("/repeat", weeks=0) == 422
    assert post("/repeat", weeks=105) == 422
    assert post("/repeat", period_type="month", period_start="2026-10", weeks=2) == 422


def test_requires_authentication(client: TestClient) -> None:
    params = {"period_type": "week", "period_start": "2026-09-14"}
    assert client.get("/api/v1/duration-goals", params=params).status_code in (401, 403)
    assert client.post("/api/v1/duration-goals", json=_goal()).status_code in (401, 403)
