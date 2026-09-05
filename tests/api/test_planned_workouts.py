"""Tests for GET/PUT/DELETE /planned-workouts -- workout_syntax.py's own parser is exhaustively
covered by tests/test_workout_syntax.py and the push orchestration by
tests/test_planned_workouts.py; these check the router's own responsibilities: the
available=False no-workout-scheduled case, upsert-not-duplicate + step replacement, the
date-range list, the recurring endpoint's date math and skip-existing behavior, and that the
push trigger runs (and fails cleanly, no token store present) end to end.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from perseverer.db.schema import planned_workout


def test_get_unavailable_when_no_workout_scheduled(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.get("/api/v1/planned-workouts/2026-09-01", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["steps"] == []


def test_put_creates_and_parses_steps(client: TestClient, auth_headers: dict[str, str]) -> None:
    put = client.put(
        "/api/v1/planned-workouts/2026-09-01",
        json={
            "sport": "running",
            "name": "Tempo run",
            "source_text": "Warmup 10m\n\n4x\n3m 5:00-5:10/km Pace\n2m Z2 HR\n\nCooldown 5m",
        },
        headers=auth_headers,
    )
    assert put.status_code == 200
    body = put.json()
    assert body["available"] is True
    assert body["sport"] == "running"
    assert len(body["steps"]) == 5
    assert body["steps"][0]["intensity"] == "warmup"
    assert body["parse_errors"] == []
    assert body["push_status"] == "draft"
    assert body["estimated_duration_s"] == 2100.0

    get = client.get("/api/v1/planned-workouts/2026-09-01", headers=auth_headers)
    assert get.status_code == 200
    assert get.json()["name"] == "Tempo run"
    assert len(get.json()["steps"]) == 5


def test_put_upserts_and_replaces_steps_rather_than_duplicating(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    client.put(
        "/api/v1/planned-workouts/2026-09-01",
        json={"sport": "running", "source_text": "Warmup 10m\nCooldown 5m"},
        headers=auth_headers,
    )
    put2 = client.put(
        "/api/v1/planned-workouts/2026-09-01",
        json={"sport": "running", "source_text": "Warmup 20m"},
        headers=auth_headers,
    )
    assert put2.status_code == 200
    assert len(put2.json()["steps"]) == 1
    assert put2.json()["steps"][0]["duration_time_s"] == 1200

    with engine.connect() as conn:
        rows = conn.execute(select(planned_workout)).fetchall()
    assert len(rows) == 1  # upserted, not a second row


def test_put_with_parse_errors_still_saves_and_reports_them(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    put = client.put(
        "/api/v1/planned-workouts/2026-09-01",
        json={"sport": "running", "source_text": "10m sparkles"},
        headers=auth_headers,
    )
    assert put.status_code == 200
    body = put.json()
    assert len(body["steps"]) == 1
    assert len(body["parse_errors"]) == 1
    assert body["parse_errors"][0]["line_no"] == 1


class TestYogaAndBoulderingPlaceholders:
    """No structured syntax at all for these two sports (the user's own explicit scoping) --
    just a name, a duration_minutes, and a display-only scheduled_time."""

    def test_put_stores_duration_and_time_with_no_steps(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        put = client.put(
            "/api/v1/planned-workouts/2026-09-01",
            json={
                "sport": "yoga",
                "name": "Evening yoga",
                "scheduled_time": "18:30",
                "duration_minutes": 45,
            },
            headers=auth_headers,
        )
        assert put.status_code == 200
        body = put.json()
        assert body["sport"] == "yoga"
        assert body["scheduled_time"] == "18:30"
        assert body["estimated_duration_s"] == 2700.0
        assert body["steps"] == []
        assert body["push_status"] == "draft"

    def test_source_text_is_kept_as_freeform_notes_not_parsed(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        put = client.put(
            "/api/v1/planned-workouts/2026-09-01",
            json={
                "sport": "bouldering",
                "duration_minutes": 90,
                "source_text": "V4 project session, bring the crash pad",
            },
            headers=auth_headers,
        )
        assert put.status_code == 200
        body = put.json()
        assert body["source_text"] == "V4 project session, bring the crash pad"
        assert body["steps"] == []
        assert body["parse_errors"] == []

    def test_malformed_scheduled_time_422s(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        r = client.put(
            "/api/v1/planned-workouts/2026-09-01",
            json={"sport": "yoga", "duration_minutes": 45, "scheduled_time": "6:30pm"},
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_push_reaches_garmin_rather_than_being_rejected_for_sport(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        client.put(
            "/api/v1/planned-workouts/2026-09-01",
            json={"sport": "yoga", "duration_minutes": 45},
            headers=auth_headers,
        )
        push = client.post("/api/v1/planned-workouts/2026-09-01/push", headers=auth_headers)
        assert push.status_code == 200

        # No Garmin token store in this test's tmp_path, so the push still fails -- but the
        # failure must come from the auth step, never from an early "sport not supported" gate
        # (that gate no longer exists for yoga/bouldering).
        get = client.get("/api/v1/planned-workouts/2026-09-01", headers=auth_headers)
        assert get.json()["push_status"] == "push_failed"
        assert "not supported" not in (get.json()["push_error"] or "")


def test_editing_a_pushed_workout_resets_status_to_draft(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    client.put(
        "/api/v1/planned-workouts/2026-09-01",
        json={"sport": "running", "source_text": "Warmup 10m"},
        headers=auth_headers,
    )
    with engine.connect() as conn:
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.local_date == "2026-09-01")
            .values(push_status="pushed", garmin_workout_id=123)
        )
        conn.commit()

    put = client.put(
        "/api/v1/planned-workouts/2026-09-01",
        json={"sport": "running", "source_text": "Warmup 20m"},
        headers=auth_headers,
    )
    assert put.json()["push_status"] == "draft"


def test_list_returns_workouts_within_range(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    client.put(
        "/api/v1/planned-workouts/2026-09-01",
        json={"sport": "running", "name": "In range", "source_text": "Warmup 10m"},
        headers=auth_headers,
    )
    client.put(
        "/api/v1/planned-workouts/2026-10-15",
        json={"sport": "running", "name": "Out of range", "source_text": "Warmup 10m"},
        headers=auth_headers,
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


def test_delete_removes_the_row(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    client.put(
        "/api/v1/planned-workouts/2026-09-01",
        json={"sport": "running", "source_text": "Warmup 10m"},
        headers=auth_headers,
    )
    d = client.delete("/api/v1/planned-workouts/2026-09-01", headers=auth_headers)
    assert d.status_code == 200

    r = client.get("/api/v1/planned-workouts/2026-09-01", headers=auth_headers)
    assert r.json()["available"] is False


def test_delete_nonexistent_404s(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.delete("/api/v1/planned-workouts/2026-09-01", headers=auth_headers)
    assert r.status_code == 404


def test_push_trigger_runs_and_marks_failed_with_no_token_store(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    client.put(
        "/api/v1/planned-workouts/2026-09-01",
        json={"sport": "running", "source_text": "Warmup 10m"},
        headers=auth_headers,
    )
    push = client.post("/api/v1/planned-workouts/2026-09-01/push", headers=auth_headers)
    assert push.status_code == 200
    assert push.json()["triggered"] is True

    # TestClient runs BackgroundTasks synchronously before returning, so the push has already
    # been attempted (and failed cleanly -- no Garmin token store in this test's tmp_path).
    get = client.get("/api/v1/planned-workouts/2026-09-01", headers=auth_headers)
    assert get.json()["push_status"] == "push_failed"
    assert get.json()["push_error"]


def test_push_trigger_404s_for_nonexistent_workout(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post("/api/v1/planned-workouts/2026-09-01/push", headers=auth_headers)
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
        assert body["skipped_dates"] == []

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

    def test_skips_a_date_that_already_has_a_workout(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        client.put(
            "/api/v1/planned-workouts/2026-09-08",
            json={"sport": "running", "name": "Existing", "source_text": "Warmup 5m"},
            headers=auth_headers,
        )
        r = client.post(
            "/api/v1/planned-workouts/recurring",
            json={
                "local_date": "2026-09-01",
                "sport": "running",
                "source_text": "Warmup 10m",
                "frequency": "weekly",
                "count": 2,
            },
            headers=auth_headers,
        )
        body = r.json()
        assert body["created_dates"] == ["2026-09-01"]
        assert body["skipped_dates"] == ["2026-09-08"]

        # The skipped date's own workout was never touched.
        existing = client.get("/api/v1/planned-workouts/2026-09-08", headers=auth_headers)
        assert existing.json()["name"] == "Existing"

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
    r = client.get("/api/v1/planned-workouts/2026-09-01")
    assert r.status_code == 401
