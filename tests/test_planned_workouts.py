"""Tests for planned_workouts.py: build_running_workout's Garmin-JSON construction (pace/HR/
zone/cadence targets, repeat-group step ordering) and push_planned_workout's DB orchestration
(row write-back on success/failure, 429 propagation). The adapter's own push mechanics (upload/
schedule/delete, 429-abort-no-retry) are covered separately in
tests/adapters/test_garmin_connect_push.py.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
from sqlalchemy import Engine, select

from perseverer.adapters.garmin_connect import RateLimitSettings
from perseverer.db.engine import make_engine
from perseverer.db.schema import (
    athlete,
    athlete_hr_zone_config,
    metadata,
    planned_workout,
    planned_workout_step,
)
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.planned_workouts import (
    PlannedStepLike,
    WorkoutBuildError,
    build_exercise_workout,
    build_running_workout,
    push_planned_workout,
)


class TestBuildRunningWorkout:
    def test_pace_target_converts_to_mps_target_values(self) -> None:
        steps = [
            PlannedStepLike(
                0, "distance", None, 2000, "pace", 3.0, 3.5, None, None, None, None, None, None
            )
        ]
        workout = build_running_workout("Run", steps, 600, hr_boundaries=None, max_hr_bpm=None)
        step = workout.workoutSegments[0].workoutSteps[0]
        assert step.model_dump()["targetValueOne"] == 3.0
        assert step.model_dump()["targetValueTwo"] == 3.5
        assert step.targetType["workoutTargetTypeKey"] == "pace.zone"

    def test_hr_zone_target_resolves_against_athlete_zones(self) -> None:
        steps = [
            PlannedStepLike(
                0, "time", 300, None, "heart_rate", None, None, 2, None, None, None, None, None
            )
        ]
        workout = build_running_workout(
            "Run", steps, 300, hr_boundaries=(120, 150, 165, 175), max_hr_bpm=190
        )
        step = workout.workoutSegments[0].workoutSteps[0].model_dump()
        assert step["targetValueOne"] == 121.0
        assert step["targetValueTwo"] == 150.0
        assert step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"

    def test_hr_zone_target_without_configured_zones_raises(self) -> None:
        steps = [
            PlannedStepLike(
                0, "time", 300, None, "heart_rate", None, None, 2, None, None, None, None, None
            )
        ]
        with pytest.raises(WorkoutBuildError):
            build_running_workout("Run", steps, 300, hr_boundaries=None, max_hr_bpm=None)

    def test_no_target_step_uses_no_target_dict(self) -> None:
        steps = [
            PlannedStepLike(
                0, "time", 300, None, None, None, None, None, None, None, None, None, None
            )
        ]
        workout = build_running_workout("Run", steps, 300, hr_boundaries=None, max_hr_bpm=None)
        step = workout.workoutSegments[0].workoutSteps[0]
        assert step.targetType["workoutTargetTypeKey"] == "no.target"

    def test_cadence_rides_alongside_pace_as_secondary_target(self) -> None:
        steps = [
            PlannedStepLike(
                0, "time", 180, None, "pace", 3.0, 3.5, None, 170, 180, None, None, None
            )
        ]
        workout = build_running_workout("Run", steps, 180, hr_boundaries=None, max_hr_bpm=None)
        step = workout.workoutSegments[0].workoutSteps[0].model_dump()
        assert step["secondaryTargetValueOne"] == 170.0
        assert step["secondaryTargetValueTwo"] == 180.0

    def test_repeat_block_becomes_repeat_group_with_ascending_step_order(self) -> None:
        steps = [
            PlannedStepLike(
                0, "time", 600, None, None, None, None, None, None, None, "warmup", None, None
            ),
            PlannedStepLike(
                1, "time", 180, None, None, None, None, None, None, None, None, None, None
            ),
            PlannedStepLike(
                2, "time", 120, None, None, None, None, None, None, None, "recovery", None, None
            ),
            PlannedStepLike(
                3,
                "repeat_until_steps_cmplt",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                1,
                4,
            ),
            PlannedStepLike(
                4, "time", 300, None, None, None, None, None, None, None, "cooldown", None, None
            ),
        ]
        workout = build_running_workout("Run", steps, 2100, hr_boundaries=None, max_hr_bpm=None)
        top_level = workout.workoutSegments[0].workoutSteps
        assert len(top_level) == 3  # warmup, repeat group, cooldown
        assert top_level[0].stepOrder == 1
        repeat_group = top_level[1]
        assert repeat_group.numberOfIterations == 4
        assert len(repeat_group.workoutSteps) == 2
        assert repeat_group.stepOrder == 2
        assert repeat_group.workoutSteps[0].stepOrder == 3
        assert repeat_group.workoutSteps[1].stepOrder == 4
        assert top_level[2].stepOrder == 5

    def test_no_steps_raises(self) -> None:
        with pytest.raises(WorkoutBuildError):
            build_running_workout("Run", [], 0, hr_boundaries=None, max_hr_bpm=None)


class TestBuildExerciseWorkout:
    """hiit/strength_training: real, named Garmin exercises -- wire format live-verified
    (2026-09-05, see docs/adr/0015-scheduled-workouts.md) against the athlete's own account."""

    def test_reps_based_step_with_weight(self) -> None:
        steps = [
            PlannedStepLike(
                0,
                "reps",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                duration_reps=10,
                exercise_category="BENCH_PRESS",
                exercise_name="",
                weight_kg=60,
            )
        ]
        workout = build_exercise_workout("strength_training", "Push day", steps, 300)
        assert workout.sportType["sportTypeKey"] == "strength_training"
        step = workout.workoutSegments[0].workoutSteps[0].model_dump()
        assert step["endCondition"]["conditionTypeKey"] == "reps"
        assert step["endConditionValue"] == 10.0
        assert step["category"] == "BENCH_PRESS"
        assert step["exerciseName"] == ""
        assert step["weightValue"] == 60000.0
        assert step["weightUnit"]["unitKey"] == "kilogram"

    def test_time_based_step_with_no_weight(self) -> None:
        steps = [
            PlannedStepLike(
                0,
                "time",
                45,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                exercise_category="BURPEE",
                exercise_name="",
            )
        ]
        workout = build_exercise_workout("hiit", "HIIT circuit", steps, 300)
        assert workout.sportType["sportTypeKey"] == "hiit"
        step = workout.workoutSegments[0].workoutSteps[0].model_dump()
        assert step["endCondition"]["conditionTypeKey"] == "time"
        assert step["endConditionValue"] == 45.0
        assert step["category"] == "BURPEE"
        assert step.get("weightValue") is None

    def test_rest_step_has_no_exercise_fields(self) -> None:
        steps = [
            PlannedStepLike(
                0, "time", 90, None, None, None, None, None, None, None, "rest", None, None
            )
        ]
        workout = build_exercise_workout("strength_training", "Rest", steps, 90)
        step = workout.workoutSegments[0].workoutSteps[0].model_dump()
        assert step["stepType"]["stepTypeKey"] == "rest"
        assert step.get("category") is None

    def test_sets_wrap_in_a_repeat_group(self) -> None:
        steps = [
            PlannedStepLike(
                0,
                "reps",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                duration_reps=10,
                exercise_category="SQUAT",
                exercise_name="",
            ),
            PlannedStepLike(
                1, "time", 90, None, None, None, None, None, None, None, "rest", None, None
            ),
            PlannedStepLike(
                2,
                "repeat_until_steps_cmplt",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                0,
                3,
            ),
        ]
        workout = build_exercise_workout("strength_training", "Leg day", steps, 600)
        top_level = workout.workoutSegments[0].workoutSteps
        assert len(top_level) == 1
        repeat_group = top_level[0]
        assert repeat_group.numberOfIterations == 3
        assert len(repeat_group.workoutSteps) == 2

    def test_unknown_sport_raises(self) -> None:
        with pytest.raises(WorkoutBuildError):
            build_exercise_workout("fitness", "Whatever", [], 0)

    def test_no_steps_raises(self) -> None:
        with pytest.raises(WorkoutBuildError):
            build_exercise_workout("hiit", "Empty", [], 0)


class FakePushGarminClient:
    def __init__(
        self, *, raise_on_login: Exception | None = None, next_workout_id: int = 99
    ) -> None:
        self._raise_on_login = raise_on_login
        self.next_workout_id = next_workout_id
        self.uploaded: list[Any] = []
        self.scheduled: list[tuple[int, str]] = []
        self.deleted: list[int] = []

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        if self._raise_on_login:
            raise self._raise_on_login
        return None, None

    def delete_workout(self, workout_id: int) -> None:
        self.deleted.append(workout_id)

    def upload_workout(self, workout_json: dict[str, Any]) -> dict[str, Any]:
        self.uploaded.append(workout_json)
        return {"workoutId": self.next_workout_id}

    def schedule_workout(self, workout_id: int, date_str: str) -> dict[str, Any]:
        self.scheduled.append((workout_id, date_str))
        return {"date": date_str}


class RaisingOnUploadClient(FakePushGarminClient):
    def upload_workout(self, workout_json: dict[str, Any]) -> dict[str, Any]:
        raise GarminConnectTooManyRequestsError("429")


def _engine(tmp_path: Path) -> Engine:
    engine = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    return engine


def _insert_planned_workout(engine: Engine, *, sport: str = "running") -> int:
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        result = conn.execute(
            planned_workout.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-01",
                sport=sport,
                name="Test run",
                source_text="Warmup 10m",
                estimated_duration_s=600,
                push_status="draft",
                created_at=now,
                updated_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        workout_id = result.inserted_primary_key[0]
        conn.execute(
            planned_workout_step.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                planned_workout_id=workout_id,
                step_index=0,
                duration_type="time",
                duration_time_s=600,
                intensity="warmup",
            )
        )
        conn.commit()
    assert isinstance(workout_id, int)
    return workout_id


def test_push_success_writes_pushed_status_and_workout_id(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    workout_id = _insert_planned_workout(engine)
    client = FakePushGarminClient()

    with engine.connect() as conn:
        result = push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
        assert result.success
        assert result.garmin_workout_id == 99

        row = conn.execute(
            select(planned_workout).where(planned_workout.c.id == workout_id)
        ).fetchone()
        assert row is not None
        assert row.push_status == "pushed"
        assert row.garmin_workout_id == 99
        assert row.push_error is None
    assert client.scheduled == [(99, "2026-09-01")]
    assert client.deleted == []  # never pushed before -- no stale copy to delete


def test_push_sets_garmin_description_from_the_workout_level_comment(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    workout_id = _insert_planned_workout(engine)
    with engine.connect() as conn:
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == workout_id)
            .values(comment="Easy effort today, focus on cadence.")
        )
        conn.commit()
    client = FakePushGarminClient()

    with engine.connect() as conn:
        push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
    assert client.uploaded[0]["description"] == "Easy effort today, focus on cadence."


def test_push_without_a_comment_omits_the_garmin_description_field(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    workout_id = _insert_planned_workout(engine)  # no comment set
    client = FakePushGarminClient()

    with engine.connect() as conn:
        push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
    # BaseWorkout.to_dict() excludes None fields -- description simply isn't in the payload.
    assert "description" not in client.uploaded[0]


def test_editing_a_pushed_workout_deletes_the_stale_garmin_copy(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    workout_id = _insert_planned_workout(engine)
    with engine.connect() as conn:
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == workout_id)
            .values(garmin_workout_id=7, push_status="pushed")
        )
        conn.commit()

    client = FakePushGarminClient()
    with engine.connect() as conn:
        push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
    assert client.deleted == [7]


def test_a_sport_with_no_builder_yet_fails_cleanly(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    workout_id = _insert_planned_workout(engine, sport="fitness")
    client = FakePushGarminClient()

    with engine.connect() as conn:
        result = push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
        assert not result.success
        row = conn.execute(
            select(planned_workout).where(planned_workout.c.id == workout_id)
        ).fetchone()
        assert row is not None
        assert row.push_status == "push_failed"
        assert "fitness" in (row.push_error or "")


def test_yoga_pushes_as_a_placeholder_workout(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    workout_id = _insert_planned_workout(engine, sport="yoga")
    client = FakePushGarminClient()

    with engine.connect() as conn:
        result = push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
        assert result.success
        assert client.uploaded[0]["sportType"]["sportTypeKey"] == "yoga"
        row = conn.execute(
            select(planned_workout).where(planned_workout.c.id == workout_id)
        ).fetchone()
        assert row is not None
        assert row.push_status == "pushed"


def test_strength_training_pushes_a_structured_exercise_workout(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    workout_id = _insert_planned_workout(engine, sport="strength_training")
    client = FakePushGarminClient()

    with engine.connect() as conn:
        result = push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
        assert result.success
        assert client.uploaded[0]["sportType"]["sportTypeKey"] == "strength_training"
        row = conn.execute(
            select(planned_workout).where(planned_workout.c.id == workout_id)
        ).fetchone()
        assert row is not None
        assert row.push_status == "pushed"


def test_bouldering_pushes_mapped_to_the_generic_other_sport_type(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    workout_id = _insert_planned_workout(engine, sport="bouldering")
    client = FakePushGarminClient()

    with engine.connect() as conn:
        result = push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
        assert result.success
        assert client.uploaded[0]["sportType"]["sportTypeKey"] == "other"


def test_hr_zone_step_without_configured_zones_marks_push_failed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        result = conn.execute(
            planned_workout.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                local_date="2026-09-01",
                sport="running",
                name="Test run",
                estimated_duration_s=300,
                push_status="draft",
                created_at=now,
                updated_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        workout_id = result.inserted_primary_key[0]
        conn.execute(
            planned_workout_step.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                planned_workout_id=workout_id,
                step_index=0,
                duration_type="time",
                duration_time_s=300,
                target_type="heart_rate",
                target_hr_zone=2,
            )
        )
        conn.commit()

    client = FakePushGarminClient()
    with engine.connect() as conn:
        push_result = push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
    assert not push_result.success
    assert "HR zones" in (push_result.error or "")


def test_hr_zone_step_resolves_against_configured_zones(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(
            athlete_hr_zone_config.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                max_hr_bpm=190,
                threshold_hr_bpm=165,
                resting_hr_bpm=50,
                updated_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
            )
        )
        conn.commit()
    workout_id = _insert_planned_workout(engine)
    client = FakePushGarminClient()

    with engine.connect() as conn:
        result = push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
    assert result.success


def test_rate_limit_propagates_without_marking_push_failed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    workout_id = _insert_planned_workout(engine)
    client = RaisingOnUploadClient()

    from perseverer.adapters.garmin_connect import GarminRateLimitAborted

    with engine.connect() as conn:
        with pytest.raises(GarminRateLimitAborted):
            push_planned_workout(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                planned_workout_id=workout_id,
                tokenstore_dir=tmp_path / "tokens",
                rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
                client_factory=lambda: client,
            )
        row = conn.execute(
            select(planned_workout).where(planned_workout.c.id == workout_id)
        ).fetchone()
        assert row is not None
        # Not the workout's fault -- stays "draft", retried automatically on the next run.
        assert row.push_status == "draft"


def test_auth_failure_marks_push_failed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    workout_id = _insert_planned_workout(engine)
    client = FakePushGarminClient(raise_on_login=GarminConnectAuthenticationError("no token"))

    with engine.connect() as conn:
        result = push_planned_workout(
            conn,
            athlete_id=DEFAULT_ATHLETE_ID,
            planned_workout_id=workout_id,
            tokenstore_dir=tmp_path / "tokens",
            rate_limits=RateLimitSettings(request_interval_s=0, max_requests_per_hour=999),
            client_factory=lambda: client,
        )
    assert not result.success
