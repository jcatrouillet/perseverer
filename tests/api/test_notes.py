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


def test_put_note_updates_body_and_bumps_updated_at(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/notes",
        json={"entity_type": "week", "entity_id": "2025-06-02", "body": "original"},
        headers=auth_headers,
    ).json()

    r = client.put(
        f"/api/v1/notes/{created['id']}", json={"body": "edited"}, headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["id"]
    assert body["body"] == "edited"
    # entity_type/entity_id/created_at are unchanged -- only the text (and updated_at) moves.
    assert body["entity_type"] == "week"
    assert body["entity_id"] == "2025-06-02"
    assert body["created_at"] == created["created_at"]
    assert body["updated_at"] != created["updated_at"]

    listed = client.get(
        "/api/v1/notes?entity_type=week&entity_id=2025-06-02", headers=auth_headers
    ).json()
    assert len(listed) == 1
    assert listed[0]["body"] == "edited"


def test_put_note_404s_for_nonexistent_note(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.put("/api/v1/notes/999999", json={"body": "x"}, headers=auth_headers)
    assert r.status_code == 404


def test_delete_note_removes_it(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/v1/notes",
        json={"entity_type": "day", "entity_id": "2025-06-01", "body": "to be deleted"},
        headers=auth_headers,
    ).json()

    r = client.delete(f"/api/v1/notes/{created['id']}", headers=auth_headers)
    assert r.status_code == 200

    after = client.get(
        "/api/v1/notes?entity_type=day&entity_id=2025-06-01", headers=auth_headers
    ).json()
    assert after == []


def test_delete_note_404s_for_nonexistent_note(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.delete("/api/v1/notes/999999", headers=auth_headers)
    assert r.status_code == 404


def test_note_body_round_trips_unicode_exactly(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    # Emoji (including a ZWJ sequence + variation selector), an accented Latin letter, and CJK --
    # a real point of confusion once, when a Windows terminal's own cp1252 display of a raw curl
    # response looked like mojibake; the actual stored/returned bytes were correct the whole
    # time (verified by inspecting codepoints directly, bypassing any terminal). This test pins
    # that down permanently rather than relying on eyeballing a terminal again.
    body_text = "Test emoji 🏃‍♂️ café 你好 💪"
    created = client.post(
        "/api/v1/notes",
        json={"entity_type": "day", "entity_id": "2025-06-01", "body": body_text},
        headers=auth_headers,
    ).json()
    assert created["body"] == body_text

    listed = client.get(
        "/api/v1/notes?entity_type=day&entity_id=2025-06-01", headers=auth_headers
    ).json()
    assert listed[0]["body"] == body_text

    updated = client.put(
        f"/api/v1/notes/{created['id']}",
        json={"body": body_text + " edited"},
        headers=auth_headers,
    ).json()
    assert updated["body"] == body_text + " edited"


def test_notes_require_auth(client: TestClient) -> None:
    r = client.post(
        "/api/v1/notes", json={"entity_type": "day", "entity_id": "2025-06-01", "body": "x"}
    )
    assert r.status_code == 401
    r = client.get("/api/v1/notes?entity_type=day&entity_id=2025-06-01")
    assert r.status_code == 401
    r = client.put("/api/v1/notes/1", json={"body": "x"})
    assert r.status_code == 401
    r = client.delete("/api/v1/notes/1")
    assert r.status_code == 401
