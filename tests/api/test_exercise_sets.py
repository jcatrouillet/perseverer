"""Tests for exercise_sets.py (parse + ingest of Garmin's per-set response), the adapter call that
fetches it, and the GET /activities/{id}/exercise-sets route.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from garminconnect import GarminConnectNotFoundError, GarminConnectTooManyRequestsError
from sqlalchemy import Engine, select

from perseverer.adapters.garmin_connect import (
    GarminConnectAdapter,
    GarminRateLimitAborted,
    RateLimiter,
    _has_sets,
)
from perseverer.db.schema import activity_exercise_set, activity_source_link
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.exercise_sets import ingest_exercise_sets, parse_exercise_sets
from tests.api.conftest import seed_activity

RESPONSE: dict[str, Any] = {
    "activityId": 1234,
    "exerciseSets": [
        {
            "exercises": [
                {"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS", "probability": 90.0},
                {"category": "PUSH_UP", "name": "PUSH_UP", "probability": 10.0},
            ],
            "duration": 42.5,
            "repetitionCount": 10,
            "weight": 60000.0,
            "setType": "ACTIVE",
            "startTime": "2026-11-01T17:00:00.0",
        },
        {"exercises": [], "duration": 90.0, "repetitionCount": None, "setType": "REST"},
    ],
}


def test_parse_takes_the_likeliest_exercise_and_converts_weight_to_kg() -> None:
    first, rest = parse_exercise_sets(json.dumps(RESPONSE).encode())
    assert (first.exercise_category, first.exercise_name) == ("BENCH_PRESS", "BARBELL_BENCH_PRESS")
    assert (first.reps, first.weight_kg, first.set_type) == (10, 60.0, "ACTIVE")
    assert first.start_time_utc == dt.datetime(2026, 11, 1, 17, 0, 0)
    assert (rest.exercise_category, rest.reps, rest.weight_kg, rest.set_type) == (
        None,
        None,
        None,
        "REST",
    )


@pytest.mark.parametrize(
    "content",
    [b'{"activityId": 1, "exerciseSets": null}', b'{"exerciseSets": []}', b"[]", b"not json"],
)
def test_parse_of_no_sets_is_empty(content: bytes) -> None:
    assert parse_exercise_sets(content) == []


def _link(engine: Engine, activity_id: str, external_id: str) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id=activity_id)
        conn.execute(
            activity_source_link.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                activity_id=activity_id,
                source="garmin_connect",
                external_id=external_id,
                raw_object_id=_raw_object(conn),
                ingested_at=dt.datetime(2026, 11, 1),
            )
        )
        conn.commit()


def _raw_object(conn: Any) -> int:
    from perseverer.db.schema import raw_object

    result = conn.execute(
        raw_object.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            kind="fit_activity",
            sha256="x",
            storage_path="aa/x.gz",
            byte_size=1,
            fetched_at=dt.datetime(2026, 11, 1),
            created_at=dt.datetime(2026, 11, 1),
        )
    )
    return int(result.inserted_primary_key[0])


def test_ingest_replaces_the_sets_of_the_linked_activity(engine: Engine) -> None:
    _link(engine, "a1", "1234")
    content = json.dumps(RESPONSE).encode()
    with engine.connect() as conn:
        for _ in range(2):  # idempotent
            stored = ingest_exercise_sets(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                source="garmin_connect",
                external_id="1234",
                content=content,
            )
            conn.commit()
            assert stored == 2
        rows = conn.execute(select(activity_exercise_set)).fetchall()
        assert len(rows) == 2
        # An unknown vendor id stores nothing.
        assert (
            ingest_exercise_sets(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                source="garmin_connect",
                external_id="nope",
                content=content,
            )
            == 0
        )


class _FakeClient:
    def __init__(self, behaviour: Any) -> None:
        self.behaviour = behaviour

    def get_activity_exercise_sets(self, activity_id: str) -> dict[str, Any]:
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return dict(self.behaviour)


def _adapter(behaviour: Any, tmp_path: Path) -> GarminConnectAdapter:
    adapter = GarminConnectAdapter(tmp_path, RateLimiter(0.0, 1000))
    adapter._client = _FakeClient(behaviour)
    return adapter


def test_fetch_returns_the_response_and_an_empty_one_for_404(tmp_path: Path) -> None:
    assert (
        json.loads(_adapter(RESPONSE, tmp_path).fetch_exercise_sets("1234") or b"")["activityId"]
        == 1234
    )
    empty = _adapter(GarminConnectNotFoundError("404"), tmp_path).fetch_exercise_sets("9")
    assert empty is not None and json.loads(empty) == {"activityId": "9", "exerciseSets": None}


def test_fetch_aborts_on_429_without_retrying(tmp_path: Path) -> None:
    adapter = _adapter(GarminConnectTooManyRequestsError("429"), tmp_path)
    with pytest.raises(GarminRateLimitAborted):
        adapter.fetch_exercise_sets("1")


def test_only_strength_and_hiit_activities_have_sets() -> None:
    assert _has_sets({"activityType": {"typeKey": "strength_training"}})
    assert _has_sets({"activityType": {"typeKey": "hiit"}})
    assert not _has_sets({"activityType": {"typeKey": "running"}})
    assert not _has_sets({})


def test_route_lists_the_sets_in_order(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    _link(engine, "a1", "1234")
    with engine.connect() as conn:
        ingest_exercise_sets(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            source="garmin_connect",
            external_id="1234",
            content=json.dumps(RESPONSE).encode(),
        )
        conn.commit()
    r = client.get("/api/v1/activities/a1/exercise-sets", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert [s["set_index"] for s in body] == [0, 1]
    assert body[0]["exercise_category"] == "BENCH_PRESS"
    assert body[0]["weight_kg"] == 60.0
    assert (
        client.get("/api/v1/activities/zzz/exercise-sets", headers=auth_headers).status_code == 404
    )
