"""Tests for the handwritten-notes feature of a HIIT / strength activity (routers/
activity_logbook.py, handwriting.py): photos, reading them (the vision call is faked), and the
logbook of confirmed sets.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, select

from perseverer.config import Settings
from perseverer.db.schema import activity_photo
from perseverer.handwriting import (
    DecodedExercise,
    DecodedLogbook,
    DecodedSet,
    decode_logbook_photo,
    prepare_image_for_reading,
)
from tests.api.conftest import seed_activity

DECODED = DecodedLogbook(
    legible=True,
    transcript="Bench 3x10 @ 20kg",
    exercises=[
        DecodedExercise(
            name="Bench",
            sets=[DecodedSet(reps=10, weight=20, unit="kg") for _ in range(3)],
        )
    ],
)


def _png(size: tuple[int, int] = (64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 200, 200)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def strength(engine: Engine) -> str:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="s1", sport="training", sub_sport="strength_training")
        conn.commit()
    return "s1"


def _upload(
    client: TestClient, headers: dict[str, str], content: bytes, ctype: str = "image/png"
) -> Any:
    return client.post(
        "/api/v1/activities/s1/photos",
        files={"file": ("notes.png", content, ctype)},
        headers=headers,
    )


def test_photo_is_attached_listed_served_and_deleted(
    client: TestClient, auth_headers: dict[str, str], strength: str
) -> None:
    r = _upload(client, auth_headers, _png())
    assert r.status_code == 201, r.text
    photo = r.json()
    assert photo["decode_status"] is None and photo["decoded"] is None

    listed = client.get("/api/v1/activities/s1/photos", headers=auth_headers).json()
    assert [p["id"] for p in listed] == [photo["id"]]

    image = client.get(f"/api/v1/activities/s1/photos/{photo['id']}/image", headers=auth_headers)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(image.content)).size == (64, 48)

    url = f"/api/v1/activities/s1/photos/{photo['id']}"
    assert client.delete(url, headers=auth_headers).status_code == 204
    assert client.get("/api/v1/activities/s1/photos", headers=auth_headers).json() == []
    assert client.get(f"{url}/image", headers=auth_headers).status_code == 404


def test_upload_rejects_what_is_not_a_supported_image(
    client: TestClient, auth_headers: dict[str, str], strength: str
) -> None:
    assert _upload(client, auth_headers, b"not an image").status_code == 400
    assert _upload(client, auth_headers, _png(), "image/heic").status_code == 400
    missing = client.post(
        "/api/v1/activities/nope/photos",
        files={"file": ("n.png", _png(), "image/png")},
        headers=auth_headers,
    )
    assert missing.status_code == 404


def test_reading_needs_an_api_key(
    client: TestClient, auth_headers: dict[str, str], strength: str, test_settings: Settings
) -> None:
    test_settings.anthropic_api_key = None
    photo = _upload(client, auth_headers, _png()).json()
    r = client.post(f"/api/v1/activities/s1/photos/{photo['id']}/read", headers=auth_headers)
    assert r.status_code == 503
    status = client.get("/api/v1/logbook/status", headers=auth_headers).json()
    assert status == {"can_read_handwriting": False}


def test_reading_stores_the_suggestion_on_the_photo(
    client: TestClient,
    auth_headers: dict[str, str],
    strength: str,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_settings.anthropic_api_key = "k"
    monkeypatch.setattr(
        "perseverer.api.routers.activity_logbook.decode_logbook_photo",
        lambda content, *, api_key, model: DECODED,
    )
    photo = _upload(client, auth_headers, _png()).json()
    r = client.post(f"/api/v1/activities/s1/photos/{photo['id']}/read", headers=auth_headers)
    assert r.status_code == 202 and r.json()["decode_status"] == "pending"

    # The background job has run by the time the test client returns.
    done = client.get("/api/v1/activities/s1/photos", headers=auth_headers).json()[0]
    assert done["decode_status"] == "done"
    assert done["decoded"]["exercises"][0]["name"] == "Bench"
    assert len(done["decoded"]["exercises"][0]["sets"]) == 3


def test_a_failed_reading_is_reported_not_raised(
    client: TestClient,
    auth_headers: dict[str, str],
    strength: str,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_settings.anthropic_api_key = "k"

    def boom(content: bytes, *, api_key: str, model: str) -> DecodedLogbook:
        raise RuntimeError("secret detail that must not leak")

    monkeypatch.setattr("perseverer.api.routers.activity_logbook.decode_logbook_photo", boom)
    photo = _upload(client, auth_headers, _png()).json()
    client.post(f"/api/v1/activities/s1/photos/{photo['id']}/read", headers=auth_headers)
    failed = client.get("/api/v1/activities/s1/photos", headers=auth_headers).json()[0]
    assert failed["decode_status"] == "failed"
    assert "secret" not in failed["decode_error"]


def test_a_reading_left_pending_too_long_is_reported_failed(
    client: TestClient, auth_headers: dict[str, str], strength: str, engine: Engine
) -> None:
    import datetime as dt

    photo = _upload(client, auth_headers, _png()).json()
    with engine.connect() as conn:
        conn.execute(
            activity_photo.update().values(
                decode_status="pending",
                decode_started_at=dt.datetime.now(dt.UTC).replace(tzinfo=None)
                - dt.timedelta(minutes=30),
            )
        )
        conn.commit()
    listed = client.get("/api/v1/activities/s1/photos", headers=auth_headers).json()
    assert listed[0]["id"] == photo["id"] and listed[0]["decode_status"] == "failed"


def _exercise(label: str, n: int, **fields: Any) -> dict[str, Any]:
    return {"label": label, "sets": [{"reps": 10, "weight_kg": 20.0} for _ in range(n)], **fields}


def test_logbook_keeps_exercises_and_their_order_and_replaces_on_save(
    client: TestClient, auth_headers: dict[str, str], strength: str
) -> None:
    assert client.get("/api/v1/activities/s1/logbook", headers=auth_headers).json() == {
        "exercises": []
    }
    body = {
        "exercises": [
            _exercise("Squat", 2, exercise_category="SQUAT", exercise_name="AIR_SQUAT"),
            _exercise("Bench", 3),
            _exercise("Bench", 1),  # the same label again later: a separate exercise
            {"label": "Skipped", "sets": []},  # no set: dropped
        ]
    }
    saved = client.put("/api/v1/activities/s1/logbook", json=body, headers=auth_headers).json()
    assert [(e["label"], len(e["sets"])) for e in saved["exercises"]] == [
        ("Squat", 2),
        ("Bench", 3),
        ("Bench", 1),
    ]
    assert saved["exercises"][0]["exercise_name"] == "AIR_SQUAT"

    again = client.put(
        "/api/v1/activities/s1/logbook",
        json={"exercises": [_exercise("Row", 1)]},
        headers=auth_headers,
    ).json()
    assert [e["label"] for e in again["exercises"]] == ["Row"]
    cleared = client.put(
        "/api/v1/activities/s1/logbook", json={"exercises": []}, headers=auth_headers
    ).json()
    assert cleared == {"exercises": []}


def test_logbook_validates_input(
    client: TestClient, auth_headers: dict[str, str], strength: str
) -> None:
    blank = {"exercises": [{"label": "", "sets": []}]}
    assert (
        client.put("/api/v1/activities/s1/logbook", json=blank, headers=auth_headers).status_code
        == 422
    )
    negative = {"exercises": [{"label": "x", "sets": [{"reps": -1}]}]}
    assert (
        client.put("/api/v1/activities/s1/logbook", json=negative, headers=auth_headers).status_code
        == 422
    )


def test_prepare_image_resizes_orients_and_rejects_garbage() -> None:
    big = io.BytesIO()
    Image.new("RGB", (4000, 3000)).save(big, format="PNG")
    data, media_type = prepare_image_for_reading(big.getvalue())
    assert media_type == "image/jpeg" and max(Image.open(io.BytesIO(data)).size) == 1600
    with pytest.raises(ValueError):
        prepare_image_for_reading(b"nope")


def _fake_client(parsed: DecodedLogbook | None, stop_reason: str = "end_turn") -> Any:
    calls: list[dict[str, Any]] = []

    def parse(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    client.calls = calls
    return client


def test_decode_sends_the_image_to_the_configured_model() -> None:
    fake = _fake_client(DECODED)
    result = decode_logbook_photo(_png(), api_key="k", model="claude-opus-5-5", client=fake)
    assert result == DECODED
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is DecodedLogbook
    content = call["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"


@pytest.mark.parametrize(("parsed", "stop"), [(None, "end_turn"), (DECODED, "refusal")])
def test_decode_reports_an_unusable_answer(parsed: DecodedLogbook | None, stop: str) -> None:
    with pytest.raises(ValueError):
        decode_logbook_photo(
            _png(), api_key="k", model="m", client=_fake_client(parsed, stop_reason=stop)
        )


def test_the_logbook_is_keyed_by_start_time_and_survives_the_activity_row(
    client: TestClient, auth_headers: dict[str, str], strength: str, engine: Engine
) -> None:
    """A rebuild deletes and re-creates activity rows with new ids: the logbook must follow the
    activity by its start time."""
    from perseverer.db.schema import activity

    client.put(
        "/api/v1/activities/s1/logbook",
        json={"exercises": [_exercise("Squat", 2)]},
        headers=auth_headers,
    )
    with engine.connect() as conn:
        row = conn.execute(select(activity).where(activity.c.id == "s1")).one()
        conn.execute(activity.delete().where(activity.c.id == "s1"))
        seed_activity(conn, activity_id="s2", sport="training", sub_sport="strength_training")
        assert conn.execute(select(activity.c.start_time_utc)).scalar_one() == row.start_time_utc
        conn.commit()
    after = client.get("/api/v1/activities/s2/logbook", headers=auth_headers).json()
    assert [e["label"] for e in after["exercises"]] == ["Squat"]
