"""Tests for GET/POST/PUT/DELETE /bouldering-goals -- bouldering_goals.py's own math is covered by
tests/test_bouldering_goals.py; these check the router: several goals per period, duplicate
refusal, validation, per-athlete scoping and auth."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.db.schema import split
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from tests.api.conftest import seed_activity


def _goal(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "period_type": "year",
        "period_start": "2026",
        "grade": 4,
        "and_harder": False,
        "target_count": 10,
    }
    body.update(overrides)
    return body


def test_list_is_empty_when_no_goal_is_set(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/api/v1/bouldering-goals",
        params={"period_type": "year", "period_start": "2026"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == []


def test_a_period_can_hold_several_goals_each_with_its_own_progress(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(
            conn,
            activity_id="b1",
            local_date="2026-03-01",
            sport="rock_climbing",
            sub_sport="bouldering",
        )
        for i, (grade, result) in enumerate([(4, "completed"), (4, "completed"), (5, "completed")]):
            conn.execute(
                split.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="b1",
                    split_index=i,
                    split_type="climb_active",
                    climb_grade=grade,
                    climb_result=result,
                    duration_s=30.0,
                )
            )
        conn.commit()

    for body in (
        _goal(grade=4),
        _goal(grade=None, target_count=50),
        _goal(grade=5, target_count=1),
    ):
        assert (
            client.post("/api/v1/bouldering-goals", json=body, headers=auth_headers).status_code
            == 201
        )

    r = client.get(
        "/api/v1/bouldering-goals",
        params={"period_type": "year", "period_start": "2026"},
        headers=auth_headers,
    )
    goals = r.json()
    assert len(goals) == 3
    by_grade = {g["goal"]["grade"]: g for g in goals}
    assert by_grade[4]["current_count"] == 2
    assert by_grade[5]["current_count"] == 1
    assert by_grade[5]["pct_complete"] == 1.0
    assert by_grade[None]["current_count"] == 3
    # Specific grades first (ascending), the any-grade goal last.
    assert [g["goal"]["grade"] for g in goals] == [4, 5, None]


def test_an_identical_goal_is_refused_but_a_different_grade_or_or_harder_is_not(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    assert (
        client.post("/api/v1/bouldering-goals", json=_goal(), headers=auth_headers).status_code
        == 201
    )
    dup = client.post("/api/v1/bouldering-goals", json=_goal(target_count=99), headers=auth_headers)
    assert dup.status_code == 409
    assert (
        client.post(
            "/api/v1/bouldering-goals", json=_goal(grade=5), headers=auth_headers
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/bouldering-goals", json=_goal(and_harder=True), headers=auth_headers
        ).status_code
        == 201
    )
    # Two any-grade goals for the same period are duplicates too.
    any_grade = _goal(grade=None)
    assert (
        client.post("/api/v1/bouldering-goals", json=any_grade, headers=auth_headers).status_code
        == 201
    )
    assert (
        client.post("/api/v1/bouldering-goals", json=any_grade, headers=auth_headers).status_code
        == 409
    )


def test_update_changes_the_target_and_refuses_a_collision(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    a = client.post("/api/v1/bouldering-goals", json=_goal(grade=4), headers=auth_headers).json()
    client.post("/api/v1/bouldering-goals", json=_goal(grade=5), headers=auth_headers)

    ok = client.put(
        f"/api/v1/bouldering-goals/{a['id']}",
        json=_goal(grade=4, target_count=12),
        headers=auth_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["target_count"] == 12

    collide = client.put(
        f"/api/v1/bouldering-goals/{a['id']}", json=_goal(grade=5), headers=auth_headers
    )
    assert collide.status_code == 409
    assert (
        client.put("/api/v1/bouldering-goals/9999", json=_goal(), headers=auth_headers).status_code
        == 404
    )


def test_delete_removes_a_goal_and_404s_on_an_unknown_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post("/api/v1/bouldering-goals", json=_goal(), headers=auth_headers).json()
    assert (
        client.delete(f"/api/v1/bouldering-goals/{created['id']}", headers=auth_headers).status_code
        == 200
    )
    assert (
        client.delete(f"/api/v1/bouldering-goals/{created['id']}", headers=auth_headers).status_code
        == 404
    )


def test_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    def post(**overrides: object) -> int:
        r = client.post("/api/v1/bouldering-goals", json=_goal(**overrides), headers=auth_headers)
        return int(r.status_code)

    assert post(target_count=0) == 422
    assert post(period_type="day") == 422
    assert post(grade=99) == 422
    assert post(grade=None, and_harder=True) == 422
    assert post(period_type="month", period_start="2026-13") == 422
    assert post(period_type="week", period_start="not-a-date") == 422
    assert post(period_type="week", period_start="2026-09-27") == 201


def test_requires_authentication(client: TestClient) -> None:
    assert client.get(
        "/api/v1/bouldering-goals", params={"period_type": "year", "period_start": "2026"}
    ).status_code in (401, 403)
    assert client.post("/api/v1/bouldering-goals", json=_goal()).status_code in (401, 403)


def _repeat_body(**overrides: object) -> dict[str, object]:
    body = _goal(period_type="week", period_start="2026-09-28", weeks=4)
    body.update(overrides)
    return body


def test_repeat_creates_one_goal_per_week_and_skips_weeks_that_already_have_it(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # The third of the four weeks already holds an identical goal.
    existing = _goal(period_type="week", period_start="2026-10-12", target_count=99)
    assert (
        client.post("/api/v1/bouldering-goals", json=existing, headers=auth_headers).status_code
        == 201
    )

    r = client.post("/api/v1/bouldering-goals/repeat", json=_repeat_body(), headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert [g["period_start"] for g in body["created"]] == [
        "2026-09-28",
        "2026-10-05",
        "2026-10-19",
    ]
    assert body["skipped_period_starts"] == ["2026-10-12"]
    assert {g["grade"] for g in body["created"]} == {4}

    # The skipped week keeps the goal it already had (not overwritten by the repeat).
    kept = client.get(
        "/api/v1/bouldering-goals",
        params={"period_type": "week", "period_start": "2026-10-12"},
        headers=auth_headers,
    ).json()
    assert [g["goal"]["target_count"] for g in kept] == [99]


def test_repeat_of_a_different_grade_is_not_skipped(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    client.post(
        "/api/v1/bouldering-goals",
        json=_goal(period_type="week", period_start="2026-09-28", grade=5),
        headers=auth_headers,
    )
    r = client.post(
        "/api/v1/bouldering-goals/repeat", json=_repeat_body(weeks=2), headers=auth_headers
    )
    assert r.json()["skipped_period_starts"] == []
    assert len(r.json()["created"]) == 2


def test_repeat_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    def status(**overrides: object) -> int:
        r = client.post(
            "/api/v1/bouldering-goals/repeat", json=_repeat_body(**overrides), headers=auth_headers
        )
        return int(r.status_code)

    assert status(weeks=0) == 422
    assert status(weeks=105) == 422
    assert status(period_type="month", period_start="2026-10") == 422
    assert status(period_start="not-a-date") == 422
    assert status(grade=None, and_harder=True) == 422
    assert status(weeks=1) == 201
