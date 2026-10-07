"""GarminConnectAdapter.push_planned_workout against a fake Garmin client (no real network calls,
ever) -- the one place this app writes to a third-party account rather than only reading from it
(docs/ARCHITECTURE.md). Covers the same non-negotiable properties every other
adapter method in this codebase is held to: abort immediately on a 429, never retry, never
construct its own credentialed client. DB write-back (`push_status`/`garmin_workout_id`) is
covered by tests/test_planned_workouts.py, which exercises the full `push_planned_workout`
orchestration this adapter method sits underneath.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from garminconnect import GarminConnectTooManyRequestsError
from garminconnect.workout import BaseWorkout, RunningWorkout, WorkoutSegment, create_warmup_step

from perseverer.adapters.garmin_connect import (
    GarminConnectAdapter,
    GarminRateLimitAborted,
    RateLimiter,
)


class FakePushClient:
    """Stands in for `garminconnect.Garmin`, covering only the three methods
    `push_planned_workout` calls. Deliberately has no credential-carrying constructor
    parameters, same invariant `test_garmin_connect.py::FakeGarminClient` documents."""

    def __init__(
        self,
        *,
        raise_on_delete: bool = False,
        raise_on_upload: bool = False,
        raise_on_schedule: bool = False,
        next_workout_id: int = 42,
    ) -> None:
        self.raise_on_delete = raise_on_delete
        self.raise_on_upload = raise_on_upload
        self.raise_on_schedule = raise_on_schedule
        self.next_workout_id = next_workout_id
        self.deleted_ids: list[int] = []
        self.uploaded_workouts: list[Any] = []
        self.scheduled: list[tuple[int, str]] = []

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        return None, None

    def delete_workout(self, workout_id: int) -> None:
        if self.raise_on_delete:
            raise GarminConnectTooManyRequestsError("429")
        self.deleted_ids.append(workout_id)

    def upload_workout(self, workout_json: dict[str, Any]) -> dict[str, Any]:
        if self.raise_on_upload:
            raise GarminConnectTooManyRequestsError("429")
        self.uploaded_workouts.append(workout_json)
        return {"workoutId": self.next_workout_id, "workoutName": workout_json["workoutName"]}

    def schedule_workout(self, workout_id: int, date_str: str) -> dict[str, Any]:
        if self.raise_on_schedule:
            raise GarminConnectTooManyRequestsError("429")
        self.scheduled.append((workout_id, date_str))
        return {"date": date_str}


def _workout() -> RunningWorkout:
    return RunningWorkout(
        workoutName="Test run",
        estimatedDurationInSecs=600,
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 1, "sportTypeKey": "running"},
                workoutSteps=[create_warmup_step(600.0)],
            )
        ],
    )


def _yoga_workout() -> BaseWorkout:
    # A plain BaseWorkout, not a RunningWorkout -- exactly what build_placeholder_workout
    # produces for yoga/bouldering. upload_running_workout() would reject this with a TypeError
    # (isinstance-checked inside the real garminconnect library); push_planned_workout must go
    # through the generic upload_workout() instead -- see its own docstring.
    return BaseWorkout(
        workoutName="Yoga",
        sportType={"sportTypeId": 7, "sportTypeKey": "yoga", "displayOrder": 7},
        estimatedDurationInSecs=2700,
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 7, "sportTypeKey": "yoga", "displayOrder": 7},
                workoutSteps=[create_warmup_step(2700.0)],
            )
        ],
    )


def _adapter(tmp_path: Path, **client_kwargs: Any) -> tuple[GarminConnectAdapter, FakePushClient]:
    client = FakePushClient(**client_kwargs)

    def factory() -> FakePushClient:
        return client

    adapter = GarminConnectAdapter(tmp_path / "tokens", RateLimiter(0, 999), client_factory=factory)
    adapter.authenticate()
    return adapter, client


def test_new_workout_uploads_and_schedules_without_deleting(tmp_path: Path) -> None:
    adapter, client = _adapter(tmp_path)
    workout_id = adapter.push_planned_workout(_workout(), "2026-09-01")
    assert workout_id == 42
    assert client.deleted_ids == []
    assert len(client.uploaded_workouts) == 1
    assert client.scheduled == [(42, "2026-09-01")]


def test_editing_a_pushed_workout_deletes_the_stale_copy_first(tmp_path: Path) -> None:
    adapter, client = _adapter(tmp_path)
    workout_id = adapter.push_planned_workout(_workout(), "2026-09-01", existing_workout_id=7)
    assert client.deleted_ids == [7]
    assert workout_id == 42
    assert client.scheduled == [(42, "2026-09-01")]


def test_429_on_delete_aborts_without_uploading(tmp_path: Path) -> None:
    adapter, client = _adapter(tmp_path, raise_on_delete=True)
    with pytest.raises(GarminRateLimitAborted):
        adapter.push_planned_workout(_workout(), "2026-09-01", existing_workout_id=7)
    assert client.uploaded_workouts == []
    assert client.scheduled == []


def test_429_on_upload_aborts_without_scheduling(tmp_path: Path) -> None:
    adapter, client = _adapter(tmp_path, raise_on_upload=True)
    with pytest.raises(GarminRateLimitAborted):
        adapter.push_planned_workout(_workout(), "2026-09-01")
    assert client.scheduled == []


def test_429_on_schedule_aborts(tmp_path: Path) -> None:
    adapter, client = _adapter(tmp_path, raise_on_schedule=True)
    with pytest.raises(GarminRateLimitAborted):
        adapter.push_planned_workout(_workout(), "2026-09-01")
    assert len(client.uploaded_workouts) == 1  # upload already happened, only scheduling failed


def test_a_plain_baseworkout_pushes_fine_via_the_generic_upload(tmp_path: Path) -> None:
    """The yoga/bouldering case: push_planned_workout must not assume a RunningWorkout."""
    adapter, client = _adapter(tmp_path)
    workout_id = adapter.push_planned_workout(_yoga_workout(), "2026-09-01")
    assert workout_id == 42
    assert client.uploaded_workouts[0]["workoutName"] == "Yoga"
    assert client.uploaded_workouts[0]["sportType"]["sportTypeKey"] == "yoga"
