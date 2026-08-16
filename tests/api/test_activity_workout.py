"""Tests for GET /activities/{id}/workout -- the pre-planned workout structure recorded into
some activities' own FIT files (Garmin Connect's "Workout" builder). Parsing itself is covered
by tests/fit/test_parser.py; these only check the router's own responsibilities: 404, the
no-plan "None" case, and shaping stored rows into the response schema in step order.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from sporthealth.db.schema import activity_workout, activity_workout_step
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from tests.api.conftest import seed_activity


def test_404_for_unknown_activity(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get("/api/v1/activities/doesnotexist/workout", headers=auth_headers)
    assert r.status_code == 404


def test_none_when_activity_has_no_recorded_workout(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    r = client.get("/api/v1/activities/a1/workout", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() is None


def test_returns_steps_in_step_index_order_regardless_of_insertion_order(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
        conn.execute(
            activity_workout.insert().values(
                activity_id="a1",
                athlete_id=DEFAULT_ATHLETE_ID,
                name="W9 Tue · 5x1km Threshold",
                description="Focus: Lactate threshold.",
            )
        )
        # Inserted out of order -- response must still be ordered by step_index.
        for step_index, duration_type, low, high in [
            (4, "time", 2.439, 2.597),
            (0, "time", 2.439, 2.597),
            (1, "distance", 3.226, 3.333),
        ]:
            conn.execute(
                activity_workout_step.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    activity_id="a1",
                    step_index=step_index,
                    duration_type=duration_type,
                    duration_time_s=900.0 if duration_type == "time" else None,
                    duration_distance_m=1000.0 if duration_type == "distance" else None,
                    target_type="speed",
                    target_low_mps=low,
                    target_high_mps=high,
                    intensity="warmup" if step_index == 0 else "active",
                    repeat_from_step=None,
                    repeat_count=None,
                )
            )
        conn.commit()

    r = client.get("/api/v1/activities/a1/workout", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "W9 Tue · 5x1km Threshold"
    assert body["description"] == "Focus: Lactate threshold."
    assert [s["step_index"] for s in body["steps"]] == [0, 1, 4]
    assert body["steps"][1]["target_low_mps"] == 3.226


def test_returns_a_repeat_step_with_its_own_fields_and_no_target(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")
        conn.execute(
            activity_workout.insert().values(
                activity_id="a1", athlete_id=DEFAULT_ATHLETE_ID, name=None, description=None
            )
        )
        conn.execute(
            activity_workout_step.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id="a1",
                step_index=3,
                duration_type="repeat_until_steps_cmplt",
                duration_time_s=None,
                duration_distance_m=None,
                target_type=None,
                target_low_mps=None,
                target_high_mps=None,
                intensity=None,
                repeat_from_step=1,
                repeat_count=5,
            )
        )
        conn.commit()

    r = client.get("/api/v1/activities/a1/workout", headers=auth_headers)
    assert r.status_code == 200
    step = r.json()["steps"][0]
    assert step["duration_type"] == "repeat_until_steps_cmplt"
    assert step["repeat_from_step"] == 1
    assert step["repeat_count"] == 5
    assert step["target_type"] is None
