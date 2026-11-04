"""Coaching: choosing a coach, viewing as the coachee, and what a coach may change."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update

from perseverer.auth.passwords import hash_password
from perseverer.auth.tokens import create_session_token
from perseverer.db.schema import athlete, note, planned_workout
from perseverer.db.seed import DEFAULT_ATHLETE_ID

from .conftest import TEST_JWT_SECRET, seed_activity

COACH_ID = "coach-athlete"


def _make_coach(engine: Engine) -> dict[str, str]:
    """A second athlete with a login; returns the headers of a session as that athlete."""
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=COACH_ID,
                display_name="Coach Carter",
                username="carter",
                password_hash=hash_password("pw-for-tests-123"),
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.execute(
            update(athlete).where(athlete.c.id == DEFAULT_ATHLETE_ID).values(username="alex")
        )
        conn.commit()
    token, _ = create_session_token(COACH_ID, TEST_JWT_SECRET, 1)
    return {"Authorization": f"Bearer {token}"}


def _choose_coach(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.put("/api/v1/coach", headers=auth_headers, json={"username": "carter"})
    assert r.status_code == 200, r.text


def test_choose_replace_and_stop_sharing(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    coach_headers = _make_coach(engine)
    assert client.get("/api/v1/coach", headers=auth_headers).json() == {
        "coach": None,
        "coachees": [],
    }

    r = client.put("/api/v1/coach", headers=auth_headers, json={"username": "carter"})
    assert r.json()["coach"]["display_name"] == "Coach Carter"
    # Choosing the same coach again is a no-op, not a duplicate.
    client.put("/api/v1/coach", headers=auth_headers, json={"username": "carter"})

    seen_by_coach = client.get("/api/v1/coach", headers=coach_headers).json()
    assert [c["id"] for c in seen_by_coach["coachees"]] == [DEFAULT_ATHLETE_ID]

    assert client.delete("/api/v1/coach", headers=auth_headers).status_code == 204
    assert client.get("/api/v1/coach", headers=auth_headers).json()["coach"] is None
    assert client.get("/api/v1/coach", headers=coach_headers).json()["coachees"] == []


def test_cannot_choose_yourself_or_an_unknown_username(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _make_coach(engine)
    r = client.put("/api/v1/coach", headers=auth_headers, json={"username": "alex"})
    assert r.status_code == 422
    r = client.put("/api/v1/coach", headers=auth_headers, json={"username": "nobody"})
    assert r.status_code == 404


def test_coach_can_stop_coaching(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    coach_headers = _make_coach(engine)
    _choose_coach(client, auth_headers)
    r = client.delete(f"/api/v1/coach/coachees/{DEFAULT_ATHLETE_ID}", headers=coach_headers)
    assert r.status_code == 204
    assert client.get("/api/v1/coach", headers=auth_headers).json()["coach"] is None


def test_coach_views_the_coachees_data(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    coach_headers = _make_coach(engine)
    with engine.connect() as conn:
        seed_activity(conn, activity_id="theirs")
    _choose_coach(client, auth_headers)

    acting = {**coach_headers, "X-Act-As": DEFAULT_ATHLETE_ID}
    assert client.get("/api/v1/activities/theirs", headers=acting).status_code == 200
    # Without the header the coach sees only their own data.
    assert client.get("/api/v1/activities/theirs", headers=coach_headers).status_code == 404


def test_no_link_no_access_and_stopping_ends_it(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    coach_headers = _make_coach(engine)
    acting = {**coach_headers, "X-Act-As": DEFAULT_ATHLETE_ID}
    assert client.get("/api/v1/activities", headers=acting).status_code == 403

    _choose_coach(client, auth_headers)
    assert client.get("/api/v1/activities", headers=acting).status_code == 200
    client.delete("/api/v1/coach", headers=auth_headers)
    assert client.get("/api/v1/activities", headers=acting).status_code == 403


def test_a_coach_cannot_reach_settings_or_the_link_itself(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    coach_headers = _make_coach(engine)
    _choose_coach(client, auth_headers)
    acting = {**coach_headers, "X-Act-As": DEFAULT_ATHLETE_ID}
    for method, path in (
        ("PUT", "/api/v1/settings/password"),
        ("POST", "/api/v1/settings/api-key"),
        ("PUT", "/api/v1/settings/profile"),
        ("PUT", "/api/v1/coach"),
        ("DELETE", "/api/v1/coach"),
    ):
        r = client.request(method, path, headers=acting, json={})
        assert r.status_code == 403, (method, path, r.status_code)
    for path in ("/api/v1/settings/api-key", "/api/v1/settings/calendar-feed"):
        assert client.get(path, headers=acting).status_code == 403
    # Still linked after all that.
    assert client.get("/api/v1/coach", headers=auth_headers).json()["coach"] is not None


def test_a_coach_comment_belongs_to_the_athlete_and_is_signed(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    coach_headers = _make_coach(engine)
    with engine.connect() as conn:
        seed_activity(conn, activity_id="theirs")
    _choose_coach(client, auth_headers)
    acting = {**coach_headers, "X-Act-As": DEFAULT_ATHLETE_ID}

    r = client.post(
        "/api/v1/notes",
        headers=acting,
        json={
            "entity_type": "activity",
            "entity_id": "theirs",
            "body": "Nice pacing",
            "author": "x",
        },
    )
    assert r.status_code == 201
    assert r.json()["author"] == "Coach Carter"
    note_id = r.json()["id"]
    with engine.connect() as conn:
        assert conn.execute(select(note.c.athlete_id)).scalar_one() == DEFAULT_ATHLETE_ID

    r = client.put(f"/api/v1/notes/{note_id}", headers=acting, json={"body": "Edited"})
    assert r.status_code == 200
    # The athlete sees it among their own comments.
    listed = client.get(
        "/api/v1/notes?entity_type=activity&entity_id=theirs", headers=auth_headers
    ).json()
    assert [n["body"] for n in listed] == ["Edited"]


def test_coach_plans_a_workout_for_the_athlete(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    coach_headers = _make_coach(engine)
    _choose_coach(client, auth_headers)
    acting = {**coach_headers, "X-Act-As": DEFAULT_ATHLETE_ID}

    r = client.post(
        "/api/v1/planned-workouts",
        headers=acting,
        json={
            "local_date": "2026-11-05",
            "sport": "running",
            "name": "Threshold intervals",
            "duration_minutes": 60,
            "comment": "Keep the recoveries easy",
        },
    )
    assert r.status_code == 200, r.text
    workout_id = r.json()["id"]
    with engine.connect() as conn:
        assert conn.execute(select(planned_workout.c.athlete_id)).scalar_one() == DEFAULT_ATHLETE_ID
    # It is in the athlete's own plan, and the coach can change and delete it.
    mine = client.get("/api/v1/planned-workouts/by-date/2026-11-05", headers=auth_headers).json()
    assert [w["name"] for w in mine] == ["Threshold intervals"]
    r = client.put(
        f"/api/v1/planned-workouts/{workout_id}",
        headers=acting,
        json={"sport": "running", "name": "Threshold intervals v2", "duration_minutes": 50},
    )
    assert r.status_code == 200
    assert (
        client.delete(f"/api/v1/planned-workouts/{workout_id}", headers=acting).status_code == 200
    )


def test_a_coach_cannot_push_or_complete_the_athletes_workouts(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    coach_headers = _make_coach(engine)
    _choose_coach(client, auth_headers)
    acting = {**coach_headers, "X-Act-As": DEFAULT_ATHLETE_ID}
    for action in ("push", "complete", "uncomplete"):
        r = client.post(f"/api/v1/planned-workouts/1/{action}", headers=acting, json={})
        assert r.status_code == 403, action
