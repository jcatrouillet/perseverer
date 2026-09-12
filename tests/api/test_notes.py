"""Tests for POST/GET /notes."""

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.api.conftest import seed_activity


def test_post_day_note_no_existence_check(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/api/v1/notes",
        json={"entity_type": "day", "entity_id": "2025-06-01", "body": "felt great today"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["entity_type"] == "day"
    assert body["entity_id"] == "2025-06-01"
    assert body["body"] == "felt great today"
    assert body["author"] is None


def test_post_week_note_no_existence_check(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/api/v1/notes",
        json={"entity_type": "week", "entity_id": "2025-06-02", "body": "solid week overall"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["entity_type"] == "week"
    assert body["entity_id"] == "2025-06-02"
    assert body["body"] == "solid week overall"


def test_get_notes_scoped_to_week_not_day(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # A day note and a week note that happen to share the same date string are two distinct
    # entities -- entity_type is part of the key, not just entity_id.
    client.post(
        "/api/v1/notes",
        json={"entity_type": "day", "entity_id": "2025-06-02", "body": "day note"},
        headers=auth_headers,
    )
    client.post(
        "/api/v1/notes",
        json={"entity_type": "week", "entity_id": "2025-06-02", "body": "week note"},
        headers=auth_headers,
    )

    r = client.get(
        "/api/v1/notes?entity_type=week&entity_id=2025-06-02", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["body"] == "week note"


def test_post_activity_note_success(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="a1")

    r = client.post(
        "/api/v1/notes",
        json={
            "entity_type": "activity",
            "entity_id": "a1",
            "body": "hard effort",
            "author": "agent",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert r.json()["author"] == "agent"


def test_post_activity_note_404_for_unknown_activity(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/api/v1/notes",
        json={"entity_type": "activity", "entity_id": "doesnotexist", "body": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_get_notes_scoped_to_entity(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.post(
        "/api/v1/notes",
        json={"entity_type": "day", "entity_id": "2025-06-01", "body": "note A"},
        headers=auth_headers,
    )
    client.post(
        "/api/v1/notes",
        json={"entity_type": "day", "entity_id": "2025-06-02", "body": "note B"},
        headers=auth_headers,
    )

    r = client.get(
        "/api/v1/notes?entity_type=day&entity_id=2025-06-01", headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["body"] == "note A"


def test_notes_require_auth(client: TestClient) -> None:
    r = client.post(
        "/api/v1/notes", json={"entity_type": "day", "entity_id": "2025-06-01", "body": "x"}
    )
    assert r.status_code == 401
    r = client.get("/api/v1/notes?entity_type=day&entity_id=2025-06-01")
    assert r.status_code == 401
