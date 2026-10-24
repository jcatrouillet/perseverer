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
    activity,
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
    activities_by_local_date,
    activity_matches_planned_sport,
    build_exercise_workout,
    build_running_workout,
    cleanup_past_garmin_workouts,
    matching_activity_id,
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

    def test_lap_button_step_ends_on_the_button_with_a_null_condition_value(self) -> None:
        steps = [
            PlannedStepLike(
                0,
                "lap_button",
                None,
                None,
                "heart_rate",
                118.0,
                128.0,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        ]
        workout = build_running_workout("Run", steps, 600, hr_boundaries=None, max_hr_bpm=None)
        step = workout.workoutSegments[0].workoutSteps[0]
        assert step.endCondition["conditionTypeKey"] == "lap.button"
        assert step.endConditionValue is None
        # The HR target still rides on the step -- only the END condition changes.
        assert step.targetType["workoutTargetTypeKey"] == "heart.rate.zone"
        assert step.model_dump()["targetValueOne"] == 118.0
        assert step.model_dump()["targetValueTwo"] == 128.0

    def test_lap_button_estimate_never_leaks_into_the_garmin_end_condition(self) -> None:
        # A 5km estimate is Perseverer's own calendar-side figure (see workout_syntax.py). If it
        # reached Garmin as an end condition the watch would advance at 5km and swap the step's
        # targets mid-climb -- the exact failure the lap keyword exists to prevent.
        steps = [
            PlannedStepLike(
                0,
                "lap_button",
                None,
                5000,
                "heart_rate",
                118.0,
                128.0,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        ]
        workout = build_running_workout("Run", steps, 600, hr_boundaries=None, max_hr_bpm=None)
        step = workout.workoutSegments[0].workoutSteps[0]
        assert step.endCondition["conditionTypeKey"] == "lap.button"
        assert step.endConditionValue is None
        assert "5000" not in str(step.model_dump())

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
    (2026-09-05, docs/ARCHITECTURE.md) against the athlete's own account."""

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


class TestActivityMatchesPlannedSport:
    """activity_matches_planned_sport's own per-tier rules -- see its docstring for why several
    tiers accept more than one real recorded shape (a generic "training" container sport with
    the real discipline in sub_sport, confirmed against garmin_activity_summary.py's own
    GARMIN_ACTIVITY_TYPE_MAP, alongside a literal top-level sport)."""

    def test_running_accepts_the_whole_run_family(self) -> None:
        assert activity_matches_planned_sport("running", "running", None)
        assert activity_matches_planned_sport("running", "trail_running", None)
        assert activity_matches_planned_sport("running", "treadmill_running", None)
        assert not activity_matches_planned_sport("running", "walking", None)

    def test_yoga_requires_the_training_container_with_yoga_sub_sport(self) -> None:
        assert activity_matches_planned_sport("yoga", "training", "yoga")
        assert not activity_matches_planned_sport("yoga", "training", "breathing")
        assert not activity_matches_planned_sport("yoga", "yoga", None)

    def test_bouldering_requires_rock_climbing_with_bouldering_sub_sport(self) -> None:
        assert activity_matches_planned_sport("bouldering", "rock_climbing", "bouldering")
        assert not activity_matches_planned_sport("bouldering", "rock_climbing", None)
        assert not activity_matches_planned_sport("bouldering", "bouldering", None)

    def test_hiit_accepts_either_a_literal_hiit_sport_or_the_training_container(self) -> None:
        assert activity_matches_planned_sport("hiit", "hiit", None)
        assert activity_matches_planned_sport("hiit", "training", "hiit")
        assert not activity_matches_planned_sport("hiit", "training", "strength_training")

    def test_strength_training_accepts_the_strength_family_or_the_training_container(
        self,
    ) -> None:
        assert activity_matches_planned_sport("strength_training", "strength_training", None)
        assert activity_matches_planned_sport("strength_training", "training", "strength_training")
        assert not activity_matches_planned_sport("strength_training", "training", "yoga")

    def test_an_unrecognized_planned_sport_never_matches(self) -> None:
        assert not activity_matches_planned_sport("fitness", "running", None)


def _seed_activity(
    engine: Engine, *, activity_id: str, local_date: str, sport: str, sub_sport: str | None = None
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


class TestActivitiesByLocalDateAndMatchingActivityId:
    def test_matches_a_same_day_recorded_activity_of_the_right_sport(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _seed_activity(
            engine, activity_id="a1", local_date="2026-09-14", sport="training", sub_sport="yoga"
        )

        with engine.connect() as conn:
            by_date = activities_by_local_date(conn, DEFAULT_ATHLETE_ID, "2026-09-14", "2026-09-14")
        assert matching_activity_id(by_date.get("2026-09-14", []), "yoga") == "a1"

    def test_no_match_when_the_only_activity_that_day_is_a_different_sport(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        _seed_activity(engine, activity_id="a1", local_date="2026-09-14", sport="running")

        with engine.connect() as conn:
            by_date = activities_by_local_date(conn, DEFAULT_ATHLETE_ID, "2026-09-14", "2026-09-14")
        assert matching_activity_id(by_date.get("2026-09-14", []), "yoga") is None

    def test_no_match_for_a_date_with_no_recorded_activity_at_all(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with engine.connect() as conn:
            by_date = activities_by_local_date(conn, DEFAULT_ATHLETE_ID, "2026-09-14", "2026-09-14")
        assert matching_activity_id(by_date.get("2026-09-14", []), "running") is None


class TestCleanupPastGarminWorkouts:
    """Workouts pushed for days that are over come off the Garmin library, and only those."""

    TODAY = dt.date(2026, 10, 24)
    LIMITS = RateLimitSettings(request_interval_s=0, max_requests_per_hour=999)

    def _row(
        self, engine: Engine, local_date: str, garmin_id: int | None, status: str = "pushed"
    ) -> int:
        now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        with engine.connect() as conn:
            result = conn.execute(
                planned_workout.insert().values(
                    athlete_id=DEFAULT_ATHLETE_ID,
                    local_date=local_date,
                    sport="running",
                    name="Run",
                    source_text="Warmup 10m",
                    estimated_duration_s=600,
                    push_status=status,
                    garmin_workout_id=garmin_id,
                    garmin_scheduled_at=now if garmin_id else None,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.commit()
            assert result.inserted_primary_key is not None
            return int(result.inserted_primary_key[0])

    def _clean(self, engine: Engine, tmp_path: Path, client: Any) -> Any:
        with engine.connect() as conn:
            return cleanup_past_garmin_workouts(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                today=self.TODAY,
                tokenstore_dir=tmp_path / "tokens",
                rate_limits=self.LIMITS,
                client_factory=lambda: client,
            )

    def _garmin_id(self, engine: Engine, row_id: int) -> int | None:
        with engine.connect() as conn:
            value = conn.execute(
                select(planned_workout.c.garmin_workout_id).where(planned_workout.c.id == row_id)
            ).scalar_one()
        return None if value is None else int(value)

    def test_past_workouts_are_deleted_from_garmin_and_today_and_later_are_kept(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        yesterday = self._row(engine, "2026-10-23", 11)
        long_ago = self._row(engine, "2026-09-01", 12, status="push_failed")
        today = self._row(engine, "2026-10-24", 13)
        tomorrow = self._row(engine, "2026-10-25", 14)
        never_pushed = self._row(engine, "2026-09-02", None, status="draft")
        client = FakePushGarminClient()

        result = self._clean(engine, tmp_path, client)

        assert (result.deleted, result.already_gone, result.failed, result.rate_limited) == (
            2, 0, 0, False,
        )  # fmt: skip
        assert sorted(client.deleted) == [11, 12]
        assert self._garmin_id(engine, yesterday) is None
        assert self._garmin_id(engine, long_ago) is None
        assert self._garmin_id(engine, today) == 13
        assert self._garmin_id(engine, tomorrow) == 14
        assert self._garmin_id(engine, never_pushed) is None

    def test_the_local_plan_is_kept_as_history(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        row = self._row(engine, "2026-10-01", 21)
        self._clean(engine, tmp_path, FakePushGarminClient())
        with engine.connect() as conn:
            kept = conn.execute(select(planned_workout).where(planned_workout.c.id == row)).one()
        assert kept.name == "Run" and kept.push_status == "pushed"
        assert kept.garmin_workout_id is None and kept.garmin_scheduled_at is None

    def test_another_athletes_workouts_are_never_touched(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        mine = self._row(engine, "2026-10-01", 31)
        with engine.connect() as conn:
            conn.execute(
                athlete.insert().values(
                    id="other",
                    display_name="Other",
                    timezone="UTC",
                    unit_preference="metric",
                    created_at=dt.datetime.now(dt.UTC),
                )
            )
            now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
            conn.execute(
                planned_workout.insert().values(
                    athlete_id="other",
                    local_date="2026-10-01",
                    sport="running",
                    name="Theirs",
                    source_text="Warmup 10m",
                    estimated_duration_s=600,
                    push_status="pushed",
                    garmin_workout_id=32,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.commit()
        client = FakePushGarminClient()
        self._clean(engine, tmp_path, client)
        assert client.deleted == [31]
        assert self._garmin_id(engine, mine) is None

    def test_a_workout_garmin_no_longer_has_is_just_forgotten(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        row = self._row(engine, "2026-10-01", 41)

        class Gone(FakePushGarminClient):
            def delete_workout(self, workout_id: int) -> None:
                raise RuntimeError("404 Not Found")

        result = self._clean(engine, tmp_path, Gone())
        assert (result.deleted, result.already_gone, result.failed) == (0, 1, 0)
        assert self._garmin_id(engine, row) is None

    def test_another_error_leaves_it_for_the_next_run(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        broken = self._row(engine, "2026-10-01", 51)
        fine = self._row(engine, "2026-10-02", 52)

        class Flaky(FakePushGarminClient):
            def delete_workout(self, workout_id: int) -> None:
                if workout_id == 51:
                    raise RuntimeError("500 Internal Server Error")
                self.deleted.append(workout_id)

        result = self._clean(engine, tmp_path, Flaky())
        assert (result.deleted, result.failed) == (1, 1)
        assert self._garmin_id(engine, broken) == 51
        assert self._garmin_id(engine, fine) is None

    def test_a_rate_limit_stops_the_run_at_once_without_retrying(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        first = self._row(engine, "2026-10-01", 61)
        second = self._row(engine, "2026-10-02", 62)

        class Limited(FakePushGarminClient):
            calls = 0

            def delete_workout(self, workout_id: int) -> None:
                Limited.calls += 1
                if workout_id == 62:
                    raise GarminConnectTooManyRequestsError("429")
                self.deleted.append(workout_id)

        result = self._clean(engine, tmp_path, Limited())
        assert result.rate_limited is True and result.deleted == 1
        assert Limited.calls == 2  # the 429 was not retried
        assert self._garmin_id(engine, first) is None
        assert self._garmin_id(engine, second) == 62

    def test_nothing_to_clean_never_touches_garmin(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        self._row(engine, "2026-10-30", 71)  # upcoming only

        def explode() -> Any:
            raise AssertionError("must not log in to Garmin when there is nothing to delete")

        with engine.connect() as conn:
            result = cleanup_past_garmin_workouts(
                conn,
                athlete_id=DEFAULT_ATHLETE_ID,
                today=self.TODAY,
                tokenstore_dir=tmp_path / "tokens",
                rate_limits=self.LIMITS,
                client_factory=explode,
            )
        assert (result.deleted, result.already_gone, result.failed) == (0, 0, 0)

    def test_without_a_garmin_login_it_raises_for_the_caller_to_skip(self, tmp_path: Path) -> None:
        from perseverer.adapters.garmin_connect import GarminAuthRequired

        engine = _engine(tmp_path)
        self._row(engine, "2026-10-01", 81)
        client = FakePushGarminClient(raise_on_login=GarminConnectAuthenticationError("no token"))
        with pytest.raises(GarminAuthRequired):
            self._clean(engine, tmp_path, client)
