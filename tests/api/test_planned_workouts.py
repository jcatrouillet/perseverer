"""Tests for POST/GET/PUT/DELETE /planned-workouts -- workout_syntax.py's own parser is
exhaustively covered by tests/test_workout_syntax.py and the push orchestration by
tests/test_planned_workouts.py; these check the router's own responsibilities: create-vs-update
(a day may hold any number of independently id-addressed workouts), the by-date list endpoint,
the date-range list, the recurring endpoint's date math (which now always creates, never skips),
and that the push trigger runs (and fails cleanly, no token store present) end to end.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.db.schema import activity, planned_workout
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def _create(
    client: TestClient, auth_headers: dict[str, str], local_date: str, **fields: Any
) -> dict[str, Any]:
    r = client.post(
        "/api/v1/planned-workouts",
        json={"local_date": local_date, **fields},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


def _seed_activity(
    engine: Engine,
    *,
    activity_id: str,
    local_date: str,
    sport: str,
    sub_sport: str | None = None,
) -> None:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=DEFAULT_ATHLETE_ID,
                start_time_utc=now,
                utc_offset_s=0,
                local_date=local_date,
                sport=sport,
                sub_sport=sub_sport,
                duration_s=1800.0,
                moving_duration_s=1700.0,
                primary_source="fit_folder",
                created_at=now,
                updated_at=now,
            )
        )
        conn.commit()


def test_get_by_id_404s_for_nonexistent_workout(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/planned-workouts/999999", headers=auth_headers)
    assert r.status_code == 404


def test_by_date_list_is_empty_when_no_workout_scheduled(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/planned-workouts/by-date/2026-09-01", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_range_list_returns_summary_rows_including_completed_at(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    run = _create(client, auth_headers, "2026-09-01", sport="running", source_text="Warmup 10m")
    _create(client, auth_headers, "2026-09-03", sport="yoga", duration_minutes=30)
    # Outside the requested range -- must not appear.
    _create(client, auth_headers, "2026-09-10", sport="running", source_text="Warmup 10m")
    client.post(f"/api/v1/planned-workouts/{run['id']}/complete", headers=auth_headers)

    r = client.get(
        "/api/v1/planned-workouts",
        params={"start_date": "2026-09-01", "end_date": "2026-09-07"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = r.json()
    assert [row["local_date"] for row in rows] == ["2026-09-01", "2026-09-03"]
    assert rows[0]["sport"] == "running"
    assert rows[0]["completed_at"] is not None
    assert rows[1]["sport"] == "yoga"
    assert rows[1]["completed_at"] is None


def test_range_list_is_empty_outside_any_scheduled_workout(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get(
        "/api/v1/planned-workouts",
        params={"start_date": "2026-09-01", "end_date": "2026-09-07"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == []


def test_range_list_matches_a_same_day_recorded_activity_without_a_manual_complete(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    """A workout the athlete never explicitly marked done still surfaces matched_activity_id
    when a same-day, matching-sport activity was synced in -- the Week view compliance stat's
    own "it's already done, no manual click needed" signal."""
    _create(client, auth_headers, "2026-09-14", sport="yoga", duration_minutes=30)
    _seed_activity(
        engine, activity_id="a1", local_date="2026-09-14", sport="training", sub_sport="yoga"
    )

    r = client.get(
        "/api/v1/planned-workouts",
        params={"start_date": "2026-09-14", "end_date": "2026-09-14"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["completed_at"] is None
    assert rows[0]["matched_activity_id"] == "a1"


def test_range_list_matched_activity_id_is_null_without_a_qualifying_activity(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _create(client, auth_headers, "2026-09-14", sport="yoga", duration_minutes=30)
    # Recorded, but the wrong sport -- must not count as a match.
    _seed_activity(engine, activity_id="a1", local_date="2026-09-14", sport="running")

    r = client.get(
        "/api/v1/planned-workouts",
        params={"start_date": "2026-09-14", "end_date": "2026-09-14"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()[0]["matched_activity_id"] is None


def test_by_date_and_by_id_endpoints_also_expose_matched_activity_id(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    created = _create(client, auth_headers, "2026-09-14", sport="running", source_text="Warmup 10m")
    _seed_activity(engine, activity_id="a1", local_date="2026-09-14", sport="trail_running")

    by_date = client.get("/api/v1/planned-workouts/by-date/2026-09-14", headers=auth_headers)
    assert by_date.status_code == 200
    assert by_date.json()[0]["matched_activity_id"] == "a1"

    by_id = client.get(f"/api/v1/planned-workouts/{created['id']}", headers=auth_headers)
    assert by_id.status_code == 200
    assert by_id.json()["matched_activity_id"] == "a1"


def test_post_creates_and_parses_steps(client: TestClient, auth_headers: dict[str, str]) -> None:
    body = _create(
        client,
        auth_headers,
        "2026-09-01",
        sport="running",
        name="Tempo run",
        source_text="Warmup 10m\n\n4x\n3m 5:00-5:10/km Pace\n2m Z2 HR\n\nCooldown 5m",
    )
    assert body["available"] is True
    assert body["sport"] == "running"
    assert len(body["steps"]) == 5
    assert body["steps"][0]["intensity"] == "warmup"
    assert body["parse_errors"] == []
    assert body["push_status"] == "draft"
    assert body["estimated_duration_s"] == 2100.0
    # No running-load threshold pace configured in this test's own DB -- distance is still a
    # real, threshold-independent estimate; load stays None; segments still carry duration and
    # whatever zone could be determined without a threshold (the Z2 HR step's own explicit zone
    # needs no threshold at all).
    assert body["estimated_distance_m"] is not None and body["estimated_distance_m"] > 0
    assert body["estimated_load"] is None
    # segments are repeat-expanded (unlike the raw `steps` above): warmup + 4x(pace, Z2 HR) +
    # cooldown = 1 + 8 + 1.
    assert len(body["segments"]) == 10
    assert body["segments"][2]["zone"] == 2  # the first rep's "2m Z2 HR" child, explicit zone

    get = client.get(f"/api/v1/planned-workouts/{body['id']}", headers=auth_headers)
    assert get.status_code == 200
    assert get.json()["name"] == "Tempo run"
    assert len(get.json()["steps"]) == 5


def test_put_parses_an_inline_comment_onto_that_steps_own_row(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(
        client,
        auth_headers,
        "2026-09-01",
        sport="running",
        source_text="Warmup 10m # legs still sore from Tuesday",
    )
    assert created["source_text"] == "Warmup 10m # legs still sore from Tuesday"
    assert created["steps"][0]["comment"] == "legs still sore from Tuesday"

    get = client.get(f"/api/v1/planned-workouts/{created['id']}", headers=auth_headers)
    assert get.json()["steps"][0]["comment"] == "legs still sore from Tuesday"

    # A second save without a comment overwrites it (update-in-place, same as every other field).
    put2 = client.put(
        f"/api/v1/planned-workouts/{created['id']}",
        json={"sport": "running", "source_text": "Warmup 15m"},
        headers=auth_headers,
    )
    assert put2.json()["steps"][0]["comment"] is None


def test_post_stores_and_returns_the_workout_level_comment(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _create(
        client,
        auth_headers,
        "2026-09-01",
        sport="running",
        source_text="Warmup 10m",
        comment="Easy effort today, focus on cadence.",
    )
    assert body["comment"] == "Easy effort today, focus on cadence."

    get = client.get(f"/api/v1/planned-workouts/{body['id']}", headers=auth_headers)
    assert get.json()["comment"] == "Easy effort today, focus on cadence."


def test_put_updates_and_can_clear_the_workout_level_comment(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(
        client,
        auth_headers,
        "2026-09-01",
        sport="hiit",
        comment="Take it easy, shoulder is tight.",
        steps=[
            {
                "step_index": 0,
                "duration_type": "reps",
                "duration_reps": 10,
                "intensity": "active",
                "exercise_category": "PUSH_UP",
                "exercise_name": "",
            }
        ],
    )
    assert created["comment"] == "Take it easy, shoulder is tight."

    put1 = client.put(
        f"/api/v1/planned-workouts/{created['id']}",
        json={
            "sport": "hiit",
            "comment": "Actually feeling good, go hard.",
            "steps": created["steps"],
        },
        headers=auth_headers,
    )
    assert put1.json()["comment"] == "Actually feeling good, go hard."

    # Same field precedent as a step's own comment (test_put_parses_an_inline_comment_onto_that_
    # steps_own_row above): a save with no comment clears whatever was there before.
    put2 = client.put(
        f"/api/v1/planned-workouts/{created['id']}",
        json={"sport": "hiit", "steps": created["steps"]},
        headers=auth_headers,
    )
    assert put2.json()["comment"] is None


def test_put_stores_a_hiit_steps_own_comment(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _create(
        client,
        auth_headers,
        "2026-09-01",
        sport="hiit",
        steps=[
            {
                "step_index": 0,
                "duration_type": "reps",
                "duration_reps": 10,
                "intensity": "active",
                "exercise_category": "PUSH_UP",
                "exercise_name": "",
                "comment": "Full range of motion",
            }
        ],
    )
    assert body["steps"][0]["comment"] == "Full range of motion"


def test_two_posts_to_the_same_date_create_two_independent_rows(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    first = _create(client, auth_headers, "2026-09-01", sport="running", name="Morning run")
    second = _create(client, auth_headers, "2026-09-01", sport="hiit", name="Evening HIIT")
    assert first["id"] != second["id"]

    with engine.connect() as conn:
        rows = conn.execute(
            select(planned_workout).where(planned_workout.c.local_date == "2026-09-01")
        ).fetchall()
    assert len(rows) == 2

    # Each is independently gettable/editable/deletable by its own id, unaffected by the other.
    got_first = client.get(f"/api/v1/planned-workouts/{first['id']}", headers=auth_headers)
    assert got_first.json()["name"] == "Morning run"
    got_second = client.get(f"/api/v1/planned-workouts/{second['id']}", headers=auth_headers)
    assert got_second.json()["name"] == "Evening HIIT"

    d = client.delete(f"/api/v1/planned-workouts/{first['id']}", headers=auth_headers)
    assert d.status_code == 200
    deleted = client.get(f"/api/v1/planned-workouts/{first['id']}", headers=auth_headers)
    assert deleted.status_code == 404
    # The second workout on that same date is untouched.
    remaining = client.get(f"/api/v1/planned-workouts/{second['id']}", headers=auth_headers)
    assert remaining.status_code == 200


def test_put_replaces_steps_without_duplicating_the_row(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    created = _create(
        client, auth_headers, "2026-09-01", sport="running", source_text="Warmup 10m\nCooldown 5m"
    )
    put2 = client.put(
        f"/api/v1/planned-workouts/{created['id']}",
        json={"sport": "running", "source_text": "Warmup 20m"},
        headers=auth_headers,
    )
    assert put2.status_code == 200
    assert len(put2.json()["steps"]) == 1
    assert put2.json()["steps"][0]["duration_time_s"] == 1200

    with engine.connect() as conn:
        rows = conn.execute(select(planned_workout)).fetchall()
    assert len(rows) == 1  # updated in place, not a second row


def test_put_for_unknown_id_404s(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.put(
        "/api/v1/planned-workouts/999999",
        json={"sport": "running", "source_text": "Warmup 10m"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_put_with_parse_errors_still_saves_and_reports_them(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    body = _create(client, auth_headers, "2026-09-01", sport="running", source_text="10m sparkles")
    assert len(body["steps"]) == 1
    assert len(body["parse_errors"]) == 1
    assert body["parse_errors"][0]["line_no"] == 1


class TestRunningLoadEstimate:
    """distance/duration/load + the per-segment zone breakdown -- planned_workout_stats.py's own
    unit tests already cover the zone/load math itself; these just check the router wires the
    athlete's configured threshold pace through to the response."""

    def _configure_threshold_pace(
        self, client: TestClient, auth_headers: dict[str, str], sec_per_km: float = 270.0
    ) -> None:
        r = client.put(
            "/api/v1/settings/running-load",
            json={"threshold_pace_sec_per_km": sec_per_km},
            headers=auth_headers,
        )
        assert r.status_code == 200

    def test_load_and_zones_appear_once_a_threshold_pace_is_configured(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        self._configure_threshold_pace(client, auth_headers)
        body = _create(
            client,
            auth_headers,
            "2026-09-01",
            sport="running",
            source_text="Warmup 10m\n\n30s 3:30/km Pace\nrecovery 30s",
        )
        assert body["estimated_load"] is not None and body["estimated_load"] > 0
        assert body["segments"][0]["zone"] == 1  # warmup
        assert body["segments"][1]["zone"] == 5  # fast pace target, well inside repetition zone

    def test_by_date_list_also_carries_the_estimate(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        self._configure_threshold_pace(client, auth_headers)
        _create(client, auth_headers, "2026-09-01", sport="running", source_text="10m 3:30/km Pace")
        by_date = client.get("/api/v1/planned-workouts/by-date/2026-09-01", headers=auth_headers)
        assert by_date.json()[0]["estimated_load"] is not None

    def test_no_threshold_configured_yields_distance_but_no_load(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(
            client, auth_headers, "2026-09-01", sport="running", source_text="10m 3:30/km Pace"
        )
        assert body["estimated_distance_m"] is not None and body["estimated_distance_m"] > 0
        assert body["estimated_load"] is None


class TestYogaAndBoulderingPlaceholders:
    """No structured syntax at all for these two sports (the user's own explicit scoping) --
    just a name, a duration_minutes, and a display-only scheduled_time."""

    def test_creates_with_duration_and_time_and_no_steps(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(
            client,
            auth_headers,
            "2026-09-01",
            sport="yoga",
            name="Evening yoga",
            scheduled_time="18:30",
            duration_minutes=45,
        )
        assert body["sport"] == "yoga"
        assert body["scheduled_time"] == "18:30"
        assert body["estimated_duration_s"] == 2700.0
        assert body["steps"] == []
        assert body["push_status"] == "draft"
        # Distance/load/segments are running-only (planned_workout_stats.py) -- a placeholder
        # sport has no pace/HR targets to build any of that from.
        assert body["estimated_distance_m"] is None
        assert body["estimated_load"] is None
        assert body["segments"] == []

    def test_source_text_is_kept_as_freeform_notes_not_parsed(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(
            client,
            auth_headers,
            "2026-09-01",
            sport="bouldering",
            duration_minutes=90,
            source_text="V4 project session, bring the crash pad",
        )
        assert body["source_text"] == "V4 project session, bring the crash pad"
        assert body["steps"] == []
        assert body["parse_errors"] == []

    def test_malformed_scheduled_time_422s(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.post(
            "/api/v1/planned-workouts",
            json={
                "local_date": "2026-09-01",
                "sport": "yoga",
                "duration_minutes": 45,
                "scheduled_time": "6:30pm",
            },
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_push_reaches_garmin_rather_than_being_rejected_for_sport(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        created = _create(client, auth_headers, "2026-09-01", sport="yoga", duration_minutes=45)
        push = client.post(f"/api/v1/planned-workouts/{created['id']}/push", headers=auth_headers)
        assert push.status_code == 200

        # No Garmin token store in this test's tmp_path, so the push still fails -- but the
        # failure must come from the auth step, never from an early "sport not supported" gate
        # (that gate no longer exists for yoga/bouldering).
        get = client.get(f"/api/v1/planned-workouts/{created['id']}", headers=auth_headers)
        assert get.json()["push_status"] == "push_failed"
        assert "not supported" not in (get.json()["push_error"] or "")


class TestExerciseSports:
    """hiit/strength_training (EXERCISE_SPORTS, planned_workouts.py) -- steps come pre-structured
    from the exercise picker (PlannedWorkoutStepIn), never parsed from source_text."""

    def test_creates_and_reads_back_exercise_steps(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(
            client,
            auth_headers,
            "2026-09-01",
            sport="strength_training",
            name="Push day",
            steps=[
                {
                    "step_index": 0,
                    "duration_type": "reps",
                    "duration_reps": 10,
                    "intensity": "active",
                    "exercise_category": "BENCH_PRESS",
                    "exercise_name": "",
                    "weight_kg": 60.0,
                },
                {
                    "step_index": 1,
                    "duration_type": "time",
                    "duration_time_s": 60,
                    "intensity": "rest",
                },
            ],
        )
        assert body["sport"] == "strength_training"
        assert body["source_text"] is None
        assert body["parse_errors"] == []
        assert len(body["steps"]) == 2
        assert body["steps"][0]["exercise_category"] == "BENCH_PRESS"
        assert body["steps"][0]["exercise_name"] == ""
        assert body["steps"][0]["weight_kg"] == 60.0
        assert body["steps"][0]["duration_reps"] == 10
        assert body["steps"][1]["intensity"] == "rest"
        assert body["steps"][1]["exercise_category"] is None
        assert body["estimated_duration_s"] and body["estimated_duration_s"] > 0

        get = client.get(f"/api/v1/planned-workouts/{body['id']}", headers=auth_headers)
        assert get.status_code == 200
        get_body = get.json()
        assert len(get_body["steps"]) == 2
        assert get_body["steps"][0]["exercise_category"] == "BENCH_PRESS"
        assert get_body["steps"][0]["weight_kg"] == 60.0

    def test_hiit_steps_wrap_in_a_repeat_group(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(
            client,
            auth_headers,
            "2026-09-01",
            sport="hiit",
            steps=[
                {
                    "step_index": 0,
                    "duration_type": "time",
                    "duration_time_s": 30,
                    "intensity": "active",
                    "exercise_category": "BURPEE",
                    "exercise_name": "",
                    "repeat_from_step": 0,
                    "repeat_count": 3,
                },
            ],
        )
        assert body["steps"][0]["repeat_count"] == 3

    def test_push_reaches_garmin_for_exercise_sports(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        created = _create(
            client,
            auth_headers,
            "2026-09-01",
            sport="hiit",
            steps=[
                {
                    "step_index": 0,
                    "duration_type": "reps",
                    "duration_reps": 15,
                    "intensity": "active",
                    "exercise_category": "BURPEE",
                    "exercise_name": "",
                },
            ],
        )
        push = client.post(f"/api/v1/planned-workouts/{created['id']}/push", headers=auth_headers)
        assert push.status_code == 200

        # No Garmin token store in this test's tmp_path, so the push still fails -- but it must
        # fail from the auth step, never from an early "sport not supported" gate.
        get = client.get(f"/api/v1/planned-workouts/{created['id']}", headers=auth_headers)
        assert get.json()["push_status"] == "push_failed"
        assert "not supported" not in (get.json()["push_error"] or "")


def test_editing_a_pushed_workout_resets_status_to_draft(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    created = _create(client, auth_headers, "2026-09-01", sport="running", source_text="Warmup 10m")
    with engine.connect() as conn:
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == created["id"])
            .values(push_status="pushed", garmin_workout_id=123)
        )
        conn.commit()

    put = client.put(
        f"/api/v1/planned-workouts/{created['id']}",
        json={"sport": "running", "source_text": "Warmup 20m"},
        headers=auth_headers,
    )
    assert put.json()["push_status"] == "draft"


def test_list_returns_workouts_within_range(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    _create(
        client,
        auth_headers,
        "2026-09-01",
        sport="running",
        name="In range",
        source_text="Warmup 10m",
    )
    _create(
        client,
        auth_headers,
        "2026-10-15",
        sport="running",
        name="Out of range",
        source_text="Warmup 10m",
    )
    r = client.get(
        "/api/v1/planned-workouts",
        params={"start_date": "2026-09-01", "end_date": "2026-09-30"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["name"] == "In range"


def test_by_date_list_orders_by_scheduled_time_then_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    untimed = _create(
        client, auth_headers, "2026-09-01", sport="strength_training", name="No time set"
    )
    evening = _create(
        client,
        auth_headers,
        "2026-09-01",
        sport="yoga",
        name="Evening yoga",
        scheduled_time="18:30",
        duration_minutes=30,
    )
    morning = _create(
        client,
        auth_headers,
        "2026-09-01",
        sport="running",
        name="Morning run",
        scheduled_time="06:00",
        source_text="Warmup 10m",
    )

    r = client.get("/api/v1/planned-workouts/by-date/2026-09-01", headers=auth_headers)
    assert r.status_code == 200
    names = [w["name"] for w in r.json()]
    # Timed workouts sort chronologically first; the untimed one falls back to creation order
    # and sorts after every timed one.
    assert names == ["Morning run", "Evening yoga", "No time set"]
    assert [w["id"] for w in r.json()] == [morning["id"], evening["id"], untimed["id"]]


def test_delete_removes_the_row(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    created = _create(client, auth_headers, "2026-09-01", sport="running", source_text="Warmup 10m")
    d = client.delete(f"/api/v1/planned-workouts/{created['id']}", headers=auth_headers)
    assert d.status_code == 200

    r = client.get(f"/api/v1/planned-workouts/{created['id']}", headers=auth_headers)
    assert r.status_code == 404


def test_delete_nonexistent_404s(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.delete("/api/v1/planned-workouts/999999", headers=auth_headers)
    assert r.status_code == 404


def test_push_trigger_runs_and_marks_failed_with_no_token_store(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = _create(client, auth_headers, "2026-09-01", sport="running", source_text="Warmup 10m")
    push = client.post(f"/api/v1/planned-workouts/{created['id']}/push", headers=auth_headers)
    assert push.status_code == 200
    assert push.json()["triggered"] is True

    # TestClient runs BackgroundTasks synchronously before returning, so the push has already
    # been attempted (and failed cleanly -- no Garmin token store in this test's tmp_path).
    get = client.get(f"/api/v1/planned-workouts/{created['id']}", headers=auth_headers)
    assert get.json()["push_status"] == "push_failed"
    assert get.json()["push_error"]


def test_push_trigger_404s_for_nonexistent_workout(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post("/api/v1/planned-workouts/999999/push", headers=auth_headers)
    assert r.status_code == 404


class TestCompletion:
    """The athlete's own manual "I did this" marker -- independent of push_status entirely, so
    it works even for a workout never pushed to (or recorded by) Garmin at all."""

    def test_complete_sets_completed_at_even_with_no_push_at_all(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        created = _create(client, auth_headers, "2026-09-01", sport="running", source_text="10m")
        assert created["completed_at"] is None
        assert created["push_status"] == "draft"

        r = client.post(f"/api/v1/planned-workouts/{created['id']}/complete", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["completed_at"] is not None
        # Completion never touches push_status -- they're deliberately independent facts.
        assert r.json()["push_status"] == "draft"

    def test_uncomplete_clears_completed_at(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        created = _create(client, auth_headers, "2026-09-01", sport="running", source_text="10m")
        client.post(f"/api/v1/planned-workouts/{created['id']}/complete", headers=auth_headers)

        r = client.post(
            f"/api/v1/planned-workouts/{created['id']}/uncomplete", headers=auth_headers
        )
        assert r.status_code == 200
        assert r.json()["completed_at"] is None

    def test_complete_works_for_a_push_failed_workout(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        created = _create(client, auth_headers, "2026-09-01", sport="running", source_text="10m")
        client.post(f"/api/v1/planned-workouts/{created['id']}/push", headers=auth_headers)
        pushed = client.get(f"/api/v1/planned-workouts/{created['id']}", headers=auth_headers)
        assert pushed.json()["push_status"] == "push_failed"  # no token store in this test env

        r = client.post(f"/api/v1/planned-workouts/{created['id']}/complete", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["completed_at"] is not None

    def test_complete_404s_for_nonexistent_workout(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.post("/api/v1/planned-workouts/999999/complete", headers=auth_headers)
        assert r.status_code == 404

    def test_uncomplete_404s_for_nonexistent_workout(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.post("/api/v1/planned-workouts/999999/uncomplete", headers=auth_headers)
        assert r.status_code == 404


class TestRecurring:
    def test_weekly_creates_the_right_dates(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.post(
            "/api/v1/planned-workouts/recurring",
            json={
                "local_date": "2026-09-01",
                "sport": "running",
                "source_text": "Warmup 10m",
                "frequency": "weekly",
                "count": 3,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["created_dates"] == ["2026-09-01", "2026-09-08", "2026-09-15"]

    def test_carries_an_inline_step_comment_onto_every_created_occurrence(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.post(
            "/api/v1/planned-workouts/recurring",
            json={
                "local_date": "2026-09-01",
                "sport": "running",
                "source_text": "Warmup 10m # marathon block, week 3",
                "frequency": "weekly",
                "count": 2,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        for d in r.json()["created_dates"]:
            got = client.get(f"/api/v1/planned-workouts/by-date/{d}", headers=auth_headers)
            assert got.json()[0]["steps"][0]["comment"] == "marathon block, week 3"

    def test_carries_the_workout_level_comment_onto_every_created_occurrence(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.post(
            "/api/v1/planned-workouts/recurring",
            json={
                "local_date": "2026-09-01",
                "sport": "running",
                "source_text": "Warmup 10m",
                "comment": "Base-building phase -- keep it aerobic.",
                "frequency": "weekly",
                "count": 2,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        for d in r.json()["created_dates"]:
            got = client.get(f"/api/v1/planned-workouts/by-date/{d}", headers=auth_headers)
            assert got.json()[0]["comment"] == "Base-building phase -- keep it aerobic."

    def test_monthly_clamps_short_months(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.post(
            "/api/v1/planned-workouts/recurring",
            json={
                "local_date": "2026-01-31",
                "sport": "running",
                "source_text": "Warmup 10m",
                "frequency": "monthly",
                "count": 2,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["created_dates"] == ["2026-01-31", "2026-02-28"]

    def test_recurring_creates_alongside_an_existing_workout_on_that_date(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """A day can hold more than one workout now, so recurring creation never skips a date
        that already has one -- it stacks alongside it instead."""
        _create(
            client,
            auth_headers,
            "2026-09-08",
            sport="running",
            name="Existing",
            source_text="Warmup 5m",
        )
        r = client.post(
            "/api/v1/planned-workouts/recurring",
            json={
                "local_date": "2026-09-01",
                "sport": "running",
                "name": "Recurring block",
                "source_text": "Warmup 10m",
                "frequency": "weekly",
                "count": 2,
            },
            headers=auth_headers,
        )
        body = r.json()
        assert body["created_dates"] == ["2026-09-01", "2026-09-08"]

        # Both workouts on 2026-09-08 now exist, independently.
        by_date = client.get("/api/v1/planned-workouts/by-date/2026-09-08", headers=auth_headers)
        names = {w["name"] for w in by_date.json()}
        assert names == {"Existing", "Recurring block"}

    def test_invalid_frequency_422s(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        r = client.post(
            "/api/v1/planned-workouts/recurring",
            json={
                "local_date": "2026-09-01",
                "sport": "running",
                "frequency": "daily",
                "count": 2,
            },
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_neither_count_nor_until_422s(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.post(
            "/api/v1/planned-workouts/recurring",
            json={"local_date": "2026-09-01", "sport": "running", "frequency": "weekly"},
            headers=auth_headers,
        )
        assert r.status_code == 422


def test_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/planned-workouts/by-date/2026-09-01")
    assert r.status_code == 401
