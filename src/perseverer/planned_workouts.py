"""Turns a saved `planned_workout` into a real Garmin workout and runs the push -- see
`db/schema.py`'s own docstring for the storage shape and `workout_syntax.py` for how the
athlete's typed `source_text` becomes `planned_workout_step` rows for a running workout.

Kept separate from `api/routers/planned_workouts.py` (plain CRUD stays inline there, the same
balance `goals.py`/`api/routers/goals.py` already strikes -- a thin request/response router, a
service module for anything that touches more than one table or an external system) because
building the actual Garmin workout JSON needs `hr_zones.py` (to resolve a "Z2 HR" step target
against the athlete's own configured zones) and the adapter's push path, more than a router
layer should carry.

Three sport tiers, not one:

- **running** gets the full structured-syntax treatment (`build_running_workout`, parsed via
  `workout_syntax.py`).
- **yoga/bouldering** (`PLACEHOLDER_SPORTS`) are deliberately simpler placeholders -- a name, a
  duration, and a display-only time of day, no step syntax at all (`build_placeholder_workout`,
  a single no-target step for the whole duration) -- per the user's own explicit scoping ("no
  structured text syntax needed, it's just to put placeholder for those sports").
- **hiit/strength_training** (`EXERCISE_SPORTS`) get real, named Garmin exercises
  (`build_exercise_workout`) -- the athlete picks from Garmin's own 1,527-exercise catalog
  (`garminconnect.exercises`) per the user's own choice over a simpler placeholder or a free-text
  syntax. Steps are supplied already-structured by the caller (the frontend's exercise picker),
  never parsed from text -- there's no natural "text syntax" for naming a specific Garmin
  exercise the way there is for a pace or HR target.

docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from garminconnect import Garmin
from garminconnect.workout import (
    WEIGHT_UNIT_KILOGRAM,
    BaseWorkout,
    ConditionType,
    ExecutableStep,
    RepeatGroup,
    RunningWorkout,
    SportType,
    StepType,
    StrengthWorkout,
    TargetType,
    WorkoutSegment,
)
from sqlalchemy import Connection, select

from perseverer.adapters.garmin_connect import (
    GarminConnectAdapter,
    GarminRateLimitAborted,
    RateLimiter,
    RateLimitSettings,
)
from perseverer.archive import read_raw_bytes
from perseverer.db.schema import (
    activity,
    athlete_hr_zone_config,
    planned_workout,
    planned_workout_step,
    raw_object,
)
from perseverer.hr_zones import compute_hr_zone_boundaries, resolve_hr_zone_bpm
from perseverer.merge.engine import sport_family
from perseverer.workout_syntax import ParseError, parse_workout_syntax

# intensity -> (StepType id, stepTypeKey, displayOrder) -- same vocabulary
# activity_workout_step.intensity/planned_workout_step.intensity already use. Any other value
# (None, "active", or an unrecognized string) falls back to a plain INTERVAL/"main set" step.
_INTENSITY_TO_STEP_TYPE: dict[str, tuple[int, str, int]] = {
    "warmup": (StepType.WARMUP, "warmup", 1),
    "cooldown": (StepType.COOLDOWN, "cooldown", 2),
    "recovery": (StepType.RECOVERY, "recovery", 4),
    "rest": (StepType.REST, "rest", 5),
    "active": (StepType.INTERVAL, "interval", 3),
}
_DEFAULT_STEP_TYPE: tuple[int, str, int] = (StepType.INTERVAL, "interval", 3)


class WorkoutBuildError(Exception):
    """A planned workout can't be turned into a real Garmin workout as currently saved -- e.g. a
    step targets an HR zone but the athlete has never configured `athlete_hr_zone_config`.
    Surfaced to the caller (API router / worker job) as a `push_status="push_failed"` with this
    message, not a 500 -- it's a data problem the athlete can fix by editing the workout or their
    zone settings, not a bug."""


@dataclass
class PlannedStepLike:
    """The subset of `planned_workout_step`'s columns this module reads -- a plain dataclass
    (not the SQLAlchemy Row itself) so `build_running_workout` is testable with lightweight
    fixtures, same precedent as `workout_syntax.py::RecordedStepLike`."""

    step_index: int
    duration_type: str | None
    duration_time_s: float | None
    duration_distance_m: float | None
    target_type: str | None
    target_low: float | None
    target_high: float | None
    target_hr_zone: int | None
    cadence_low: int | None
    cadence_high: int | None
    intensity: str | None
    repeat_from_step: int | None
    repeat_count: int | None
    # hiit/strength_training only (see EXERCISE_SPORTS) -- appended rather than interleaved
    # above so every existing positional construction (tests, mainly) stays valid unchanged.
    duration_reps: int | None = None
    exercise_category: str | None = None
    exercise_name: str | None = None
    weight_kg: float | None = None
    # A freeform note on this specific step -- see db/schema.py::planned_workout_step's own
    # docstring. Appended last for the same positional-construction-compatibility reason as the
    # fields above.
    comment: str | None = None


_PLANNED_STEP_FIELD_NAMES = [f.name for f in fields(PlannedStepLike)]


def _step_type_dict(intensity: str | None) -> dict[str, Any]:
    step_type_id, key, display_order = _INTENSITY_TO_STEP_TYPE.get(
        intensity or "", _DEFAULT_STEP_TYPE
    )
    return {"stepTypeId": step_type_id, "stepTypeKey": key, "displayOrder": display_order}


# Garmin's own end-condition id for "advance when the athlete presses the lap button", from
# /workout-service/workout/types -- 1 in the garminconnect release this project pins
# (`ConditionType.LAP_BUTTON`, verified by reading the installed package, same discipline as
# 0015's "vendor facts verified directly" section).
#
# Read via `getattr` rather than as a plain attribute because this library renumbers this class
# between releases: 0.3.2 has no LAP_BUTTON at all AND numbers DISTANCE=1/HEART_RATE=3, where the
# pinned release numbers LAP_BUTTON=1/DISTANCE=3/HEART_RATE=6. 0.3.2 is below pyproject's own
# `garminconnect>=0.3.5` floor, so it is not a version this project would install -- the point is
# only that the churn is real and undocumented, so a future bump dropping or renaming the member
# should degrade to the known-good wire id instead of raising AttributeError mid-push.
LAP_BUTTON_CONDITION_ID = getattr(ConditionType, "LAP_BUTTON", 1)


def _end_condition(step: PlannedStepLike) -> tuple[dict[str, Any], float | None]:
    if step.duration_type == "lap_button":
        # `endConditionValue` is deliberately None, not 0.0: a lap-button step has no threshold
        # to compare against, and `ExecutableStep.endConditionValue` is already `float | None`.
        # The step's own duration_time_s/duration_distance_m, if set, are Perseverer-side
        # estimates for the calendar (see workout_syntax.LAP_BUTTON_WORD) and must NOT leak into
        # the Garmin payload, or the watch would advance on them.
        return (
            {
                "conditionTypeId": LAP_BUTTON_CONDITION_ID,
                "conditionTypeKey": "lap.button",
                "displayOrder": 1,
                "displayable": True,
            },
            None,
        )
    if step.duration_type == "distance" and step.duration_distance_m is not None:
        return (
            {
                "conditionTypeId": ConditionType.DISTANCE,
                "conditionTypeKey": "distance",
                "displayOrder": 3,
                "displayable": True,
            },
            step.duration_distance_m,
        )
    return (
        {
            "conditionTypeId": ConditionType.TIME,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        },
        step.duration_time_s or 0.0,
    )


_NO_TARGET = {
    "workoutTargetTypeId": TargetType.NO_TARGET,
    "workoutTargetTypeKey": "no.target",
    "displayOrder": 1,
}
_PACE_TARGET = {
    "workoutTargetTypeId": TargetType.PACE_ZONE,
    "workoutTargetTypeKey": "pace.zone",
    "displayOrder": 6,
}
_HR_TARGET = {
    "workoutTargetTypeId": TargetType.HEART_RATE_ZONE,
    "workoutTargetTypeKey": "heart.rate.zone",
    "displayOrder": 4,
}


def _target_fields(
    step: PlannedStepLike,
    *,
    hr_boundaries: tuple[int, int, int, int] | None,
    max_hr_bpm: float | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (targetType dict, extra ExecutableStep kwargs -- targetValueOne/Two, or
    zoneNumber). Web-confirmed wire format (docs/ARCHITECTURE.md's own
    "vendor facts" section): pace values in m/s, HR values in bpm, `zoneNumber`/targetValueOne+Two
    are mutually exclusive."""
    if step.target_type == "pace" and step.target_low is not None and step.target_high is not None:
        return _PACE_TARGET, {"targetValueOne": step.target_low, "targetValueTwo": step.target_high}

    if step.target_type == "heart_rate":
        if step.target_hr_zone is not None:
            if hr_boundaries is None or max_hr_bpm is None:
                raise WorkoutBuildError(
                    f"step targets Z{step.target_hr_zone} HR but the athlete has no configured "
                    "HR zones (Settings > HR zones)"
                )
            low, high = resolve_hr_zone_bpm(step.target_hr_zone, hr_boundaries, max_hr_bpm)
            return _HR_TARGET, {"targetValueOne": low, "targetValueTwo": high}
        if step.target_low is not None and step.target_high is not None:
            return _HR_TARGET, {
                "targetValueOne": step.target_low,
                "targetValueTwo": step.target_high,
            }

    return _NO_TARGET, {}


def _cadence_extra(step: PlannedStepLike) -> dict[str, Any]:
    """ "Ride cadence alongside the primary pace/HR target" via a `secondaryTargetType`/
    `secondaryTargetValueOne`/`secondaryTargetValueTwo` triple -- **live-verified** (2026-09-03,
    a real push + `get_workout_by_id` read-back against the athlete's own Garmin account, see
    docs/ARCHITECTURE.md's Verification section): Garmin's server accepts and
    correctly stores this shape, echoing `workoutTargetTypeKey: "cadence"` back on read (not
    `"cadence.zone"`, this function's own first guess before that verification -- matched here
    for round-trip fidelity even though Garmin's server tolerated the original guess too).
    `ExecutableStep`'s `extra="allow"` config is what lets an undocumented field like this
    through at all."""
    if step.cadence_low is None or step.cadence_high is None:
        return {}
    return {
        "secondaryTargetType": {
            "workoutTargetTypeId": TargetType.CADENCE,
            "workoutTargetTypeKey": "cadence",
            "displayOrder": 3,
        },
        "secondaryTargetValueOne": float(step.cadence_low),
        "secondaryTargetValueTwo": float(step.cadence_high),
    }


def _build_executable_step(
    step: PlannedStepLike,
    step_order: int,
    *,
    hr_boundaries: tuple[int, int, int, int] | None,
    max_hr_bpm: float | None,
) -> ExecutableStep:
    end_condition, end_value = _end_condition(step)
    target_type, target_extra = _target_fields(
        step, hr_boundaries=hr_boundaries, max_hr_bpm=max_hr_bpm
    )
    return ExecutableStep(
        stepOrder=step_order,
        stepType=_step_type_dict(step.intensity),
        endCondition=end_condition,
        endConditionValue=end_value,
        targetType=target_type,
        **target_extra,
        **_cadence_extra(step),
    )


# A single step, at whatever stepOrder build_workout_segment has assigned it. running and
# hiit/strength_training each have their own step_builder (different target/exercise semantics),
# sharing this one repeat-group assembly rather than duplicating it -- see build_workout_segment.
StepBuilder = Callable[[PlannedStepLike, int], ExecutableStep]


def build_workout_segment(
    steps: list[PlannedStepLike],
    step_builder: StepBuilder,
) -> list[ExecutableStep | RepeatGroup]:
    """The unexpanded `planned_workout_step` rows (repeat children preceding their own summary
    row -- see module docstring) into Garmin's own `ExecutableStep`/`RepeatGroup` tree.
    `stepOrder` is one flat, ascending counter across the whole segment, matching the reference
    `create_strength_set` helper's own convention: a repeat group's `stepOrder` comes *before*
    its children's (group=N, children=N+1, N+2, ...), not after. `step_builder` builds one leaf
    `ExecutableStep` at a given `stepOrder` -- this function only ever handles the repeat-group
    shape, not any target/exercise-specific fields, so running and hiit/strength_training share
    it via two different `step_builder`s (`build_running_workout`/`build_exercise_workout`)."""
    sorted_steps = sorted(steps, key=lambda s: s.step_index)
    by_index = {s.step_index: s for s in sorted_steps}
    consumed: set[int] = set()
    for s in sorted_steps:
        if s.duration_type == "repeat_until_steps_cmplt" and s.repeat_from_step is not None:
            consumed.update(range(s.repeat_from_step, s.step_index))

    segment_steps: list[ExecutableStep | RepeatGroup] = []
    counter = 1
    for s in sorted_steps:
        if s.step_index in consumed:
            continue
        if (
            s.duration_type == "repeat_until_steps_cmplt"
            and s.repeat_from_step is not None
            and s.repeat_count is not None
        ):
            group_order = counter
            counter += 1
            children: list[ExecutableStep | RepeatGroup] = []
            for idx in range(s.repeat_from_step, s.step_index):
                child = by_index.get(idx)
                if child is None:
                    continue
                children.append(step_builder(child, counter))
                counter += 1
            if not children:
                continue
            segment_steps.append(
                RepeatGroup(
                    stepOrder=group_order,
                    stepType={
                        "stepTypeId": StepType.REPEAT,
                        "stepTypeKey": "repeat",
                        "displayOrder": 6,
                    },
                    numberOfIterations=s.repeat_count,
                    workoutSteps=children,
                    endCondition={
                        "conditionTypeId": ConditionType.ITERATIONS,
                        "conditionTypeKey": "iterations",
                        "displayOrder": 7,
                        "displayable": False,
                    },
                    endConditionValue=float(s.repeat_count),
                )
            )
        else:
            segment_steps.append(step_builder(s, counter))
            counter += 1
    return segment_steps


def build_running_workout(
    name: str,
    steps: list[PlannedStepLike],
    estimated_duration_s: float,
    *,
    hr_boundaries: tuple[int, int, int, int] | None,
    max_hr_bpm: float | None,
) -> RunningWorkout:
    """A Garmin `RunningWorkout` from parsed running steps, ready to upload."""

    def step_builder(step: PlannedStepLike, step_order: int) -> ExecutableStep:
        return _build_executable_step(
            step, step_order, hr_boundaries=hr_boundaries, max_hr_bpm=max_hr_bpm
        )

    segment_steps = build_workout_segment(steps, step_builder)
    if not segment_steps:
        raise WorkoutBuildError("workout has no steps -- nothing to push")
    return RunningWorkout(
        workoutName=name,
        estimatedDurationInSecs=max(0, round(estimated_duration_s)),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={
                    "sportTypeId": SportType.RUNNING,
                    "sportTypeKey": "running",
                    "displayOrder": 1,
                },
                workoutSteps=segment_steps,
            )
        ],
    )


# sport -> (sportTypeId, sportTypeKey) for the placeholder sports. "yoga" has a real Garmin
# sport type; "bouldering" doesn't -- confirmed live against Garmin's own
# GET /workout-service/workout/types (not just the garminconnect package's own hardcoded
# SportType class): the real workout-service sport list is running/cycling/swimming/
# strength_training/cardio_training/yoga/pilates/hiit/other/multi_sport/mobility/rucking, with no
# climbing entry at all. This app already knows bouldering is really a rock-climbing sub-
# discipline for *recorded* activities (garmin_activity_summary.py's own
# sport="rock_climbing"/sub_sport="bouldering" pair, Garmin's real activity-type taxonomy) --
# it's specifically the *Workout Builder* service used to push a *planned* workout that has no
# slot for it, a real constraint of that one Garmin subsystem, not a gap this app's own data
# model or this library introduced. Falls back to SportType.OTHER (see
# docs/ARCHITECTURE.md): the workout still pushes and schedules fine,
# it just shows as "Other" rather than "Bouldering" in Garmin Connect/on the watch -- the
# workout's own `name` field still says "Bouldering" regardless.
_PLACEHOLDER_SPORT_TYPES: dict[str, tuple[int, str]] = {
    "yoga": (SportType.YOGA, "yoga"),
    "bouldering": (SportType.OTHER, "other"),
}


def build_placeholder_workout(sport: str, name: str, estimated_duration_s: float) -> BaseWorkout:
    """The yoga/bouldering case: no structured syntax at all (the user's own explicit scoping --
    "no structured text syntax needed, it's just to put placeholder for those sports") -- a
    single no-target step spanning the whole duration, wrapped in a plain `BaseWorkout` (there's
    no `YogaWorkout`/`BoulderingWorkout` subclass in `garminconnect.workout`, unlike
    `RunningWorkout` -- constructing `BaseWorkout` directly with an explicit `sportType` works
    the same way and needs no such subclass to exist). Raises `WorkoutBuildError` for any sport
    this module doesn't yet know how to build a placeholder for."""
    sport_type = _PLACEHOLDER_SPORT_TYPES.get(sport)
    if sport_type is None:
        raise WorkoutBuildError(f"no placeholder workout builder for sport {sport!r}")
    sport_type_id, sport_type_key = sport_type
    sport_type_dict = {
        "sportTypeId": sport_type_id,
        "sportTypeKey": sport_type_key,
        "displayOrder": 1,
    }
    duration_s = max(0.0, estimated_duration_s)
    return BaseWorkout(
        workoutName=name,
        sportType=sport_type_dict,
        estimatedDurationInSecs=round(duration_s),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType=sport_type_dict,
                workoutSteps=[
                    ExecutableStep(
                        stepOrder=1,
                        stepType=_step_type_dict("active"),
                        endCondition={
                            "conditionTypeId": ConditionType.TIME,
                            "conditionTypeKey": "time",
                            "displayOrder": 2,
                            "displayable": True,
                        },
                        endConditionValue=duration_s,
                        targetType=_NO_TARGET,
                    )
                ],
            )
        ],
    )


# sport -> (sportTypeId, sportTypeKey, workout class) for the exercise sports. Both are real
# Garmin workout-service sport types -- confirmed live against GET /workout-service/workout/types
# (unlike bouldering, see decision 9): hiit=9, strength_training=5. `garminconnect.workout` has a
# real `StrengthWorkout` subclass; there's no `HiitWorkout`, so HIIT uses the same plain
# `BaseWorkout` pattern `build_placeholder_workout` already does for yoga.
_EXERCISE_SPORT_TYPES: dict[str, tuple[int, str, type[BaseWorkout]]] = {
    "hiit": (SportType.HIIT, "hiit", BaseWorkout),
    "strength_training": (SportType.STRENGTH_TRAINING, "strength_training", StrengthWorkout),
}
EXERCISE_SPORTS = frozenset(_EXERCISE_SPORT_TYPES)

# A rough guess (a controlled-tempo rep commonly takes 2-4s) used only for estimated_duration_s's
# own display purposes on a "reps" step -- never sent to Garmin, which needs no duration estimate
# for a reps-based step at all (the real time depends entirely on how the athlete actually moves).
_ASSUMED_SECONDS_PER_REP = 3.0


def _build_exercise_step(step: PlannedStepLike, step_order: int) -> ExecutableStep:
    """One exercise or rest step for hiit/strength_training -- reps-based (a set: "10 reps of
    bench press") when `duration_type == "reps"`, otherwise time-based (a HIIT circuit interval,
    or a rest step between sets). Wire format for `category`/`exerciseName`/`weightValue`/
    `weightUnit` confirmed live (2026-09-05, a real push + `get_workout_by_id` read-back against
    the athlete's own Garmin account, matching `create_strength_exercise_step`'s own reference
    shape exactly) -- `weightValue` is grams, not kg, despite `weight_kg`'s own storage unit."""
    if step.duration_type == "reps" and step.duration_reps is not None:
        end_condition = {
            "conditionTypeId": ConditionType.REPS,
            "conditionTypeKey": "reps",
            "displayOrder": 10,
            "displayable": True,
        }
        end_value = float(step.duration_reps)
    else:
        end_condition = {
            "conditionTypeId": ConditionType.TIME,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        }
        end_value = step.duration_time_s or 0.0

    extra: dict[str, Any] = {}
    if step.exercise_category is not None:
        extra["category"] = step.exercise_category
        extra["exerciseName"] = step.exercise_name or ""
        if step.weight_kg is not None:
            extra["weightValue"] = float(step.weight_kg) * 1000.0
            extra["weightUnit"] = dict(WEIGHT_UNIT_KILOGRAM)

    return ExecutableStep(
        stepOrder=step_order,
        stepType=_step_type_dict(step.intensity),
        endCondition=end_condition,
        endConditionValue=end_value,
        targetType=_NO_TARGET,
        **extra,
    )


def build_exercise_workout(
    sport: str, name: str, steps: list[PlannedStepLike], estimated_duration_s: float
) -> BaseWorkout:
    """The hiit/strength_training case: real, named Garmin exercises the athlete picked from
    Garmin's own catalog (`garminconnect.exercises`) -- steps arrive already-structured (from the
    frontend's exercise picker), never parsed from text (there's no natural text syntax for
    naming a specific Garmin exercise the way there is for a pace/HR target)."""
    sport_info = _EXERCISE_SPORT_TYPES.get(sport)
    if sport_info is None:
        raise WorkoutBuildError(f"no exercise workout builder for sport {sport!r}")
    sport_type_id, sport_type_key, workout_cls = sport_info
    sport_type_dict = {
        "sportTypeId": sport_type_id,
        "sportTypeKey": sport_type_key,
        "displayOrder": 1,
    }

    segment_steps = build_workout_segment(steps, _build_exercise_step)
    if not segment_steps:
        raise WorkoutBuildError("workout has no steps -- nothing to push")
    return workout_cls(
        workoutName=name,
        sportType=sport_type_dict,
        estimatedDurationInSecs=max(0, round(estimated_duration_s)),
        workoutSegments=[
            WorkoutSegment(segmentOrder=1, sportType=sport_type_dict, workoutSteps=segment_steps)
        ],
    )


def _fetch_steps(conn: Connection, planned_workout_id: int) -> list[PlannedStepLike]:
    """Every `planned_workout_step` row for one workout, in `step_index` order -- shared by
    running and hiit/strength_training, which both keep their unexpanded steps in this same
    table (just populating a different subset of columns each)."""
    step_rows = conn.execute(
        select(planned_workout_step)
        .where(planned_workout_step.c.planned_workout_id == planned_workout_id)
        .order_by(planned_workout_step.c.step_index)
    ).fetchall()
    return [
        PlannedStepLike(**{name: getattr(r, name) for name in _PLANNED_STEP_FIELD_NAMES})
        for r in step_rows
    ]


@dataclass
class PushResult:
    success: bool
    garmin_workout_id: int | None
    error: str | None


def push_planned_workout(
    conn: Connection,
    *,
    athlete_id: str,
    planned_workout_id: int,
    tokenstore_dir: Path,
    rate_limits: RateLimitSettings,
    client_factory: Any = Garmin,
    raw_archive_dir: Path | None = None,
) -> PushResult:
    """Loads one `planned_workout` + its steps, builds the Garmin `RunningWorkout`, and pushes
    it -- called from both `POST /planned-workouts/{workout_id}/push` (manual, one workout) and the
    worker's `run_daily_workout_push` job (automatic, looping over every workout due within the
    coming week -- see worker/main.py). Never raises except `GarminRateLimitAborted`: every other
    failure (a build error, an auth failure, any Garmin API error) is caught, written back as
    `push_status="push_failed"` + `push_error`, and returned as a failed `PushResult` -- so a
    caller looping over many workouts (the worker job) doesn't need its own try/except per
    workout, and a caller pushing just one (the API route) gets a normal result to report back to
    the frontend rather than a 500. `GarminRateLimitAborted` alone propagates: it means the whole
    session hit Garmin's rate limit, not that this specific workout is broken, so it should stop
    a multi-workout loop entirely (matching `sync_garmin_connect`'s own "break, don't mark
    anything failed, let the next scheduled run retry" convention) rather than being recorded
    against this one workout.

    When the workout carries a GPX route and `raw_archive_dir` is given, the route is pushed to
    Garmin as a private course right after the workout succeeds (`_push_route_course`); a course
    failure is recorded on the row and never fails the workout.
    """
    row = conn.execute(
        select(planned_workout).where(
            planned_workout.c.id == planned_workout_id, planned_workout.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        raise ValueError(f"planned_workout {planned_workout_id} not found")

    now = datetime.now(UTC).replace(tzinfo=None)
    adapter: GarminConnectAdapter | None = None

    def _mark_failed(message: str) -> PushResult:
        conn.rollback()
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == planned_workout_id)
            .values(push_status="push_failed", push_error=message, updated_at=now)
        )
        conn.commit()
        return PushResult(success=False, garmin_workout_id=None, error=message)

    if row.sport not in ("running", *_PLACEHOLDER_SPORT_TYPES, *EXERCISE_SPORTS):
        return _mark_failed(f"push not supported yet for sport {row.sport!r}")

    try:
        if row.sport == "running":
            steps = _fetch_steps(conn, planned_workout_id)

            zone_row = conn.execute(
                select(athlete_hr_zone_config).where(
                    athlete_hr_zone_config.c.athlete_id == athlete_id
                )
            ).fetchone()
            hr_boundaries = None
            max_hr_bpm = None
            if zone_row is not None:
                hr_boundaries = compute_hr_zone_boundaries(
                    zone_row.max_hr_bpm, zone_row.threshold_hr_bpm, zone_row.resting_hr_bpm
                )
                max_hr_bpm = zone_row.max_hr_bpm

            workout: BaseWorkout = build_running_workout(
                row.name or f"Run - {row.local_date}",
                steps,
                row.estimated_duration_s or 0.0,
                hr_boundaries=hr_boundaries,
                max_hr_bpm=max_hr_bpm,
            )
        elif row.sport in EXERCISE_SPORTS:
            steps = _fetch_steps(conn, planned_workout_id)
            workout = build_exercise_workout(
                row.sport,
                row.name or f"{row.sport.replace('_', ' ').capitalize()} - {row.local_date}",
                steps,
                row.estimated_duration_s or 0.0,
            )
        else:
            workout = build_placeholder_workout(
                row.sport,
                row.name or f"{row.sport.capitalize()} - {row.local_date}",
                row.estimated_duration_s or 0.0,
            )

        # The workout's own general-guidance comment, read before any step -- every workout
        # class subclasses garminconnect.workout.BaseWorkout, which already has a real
        # `description` field (confirmed by introspecting the installed package), so this needs
        # no synthetic step. Not set for yoga/bouldering (no UI populates `row.comment` there).
        if row.comment:
            workout.description = row.comment

        rate_limiter = RateLimiter(
            rate_limits.request_interval_s, rate_limits.max_requests_per_hour
        )
        adapter = GarminConnectAdapter(tokenstore_dir, rate_limiter, client_factory=client_factory)
        adapter.authenticate()
        workout_id = adapter.push_planned_workout(
            workout, row.local_date, existing_workout_id=row.garmin_workout_id
        )
    except GarminRateLimitAborted:
        raise
    except Exception as e:  # deliberately broad -- see docstring above
        return _mark_failed(str(e))

    conn.execute(
        planned_workout.update()
        .where(planned_workout.c.id == planned_workout_id)
        .values(
            garmin_workout_id=workout_id,
            garmin_scheduled_at=now,
            push_status="pushed",
            push_error=None,
            updated_at=now,
        )
    )
    conn.commit()
    if (
        row.sport == "running"
        and row.route_raw_object_id is not None
        and raw_archive_dir is not None
        and adapter is not None
    ):
        _push_route_course(conn, adapter, row, raw_archive_dir)
    return PushResult(success=True, garmin_workout_id=workout_id, error=None)


def _push_route_course(
    conn: Connection,
    adapter: GarminConnectAdapter,
    row: Any,
    raw_archive_dir: Path,
) -> None:
    """Pushes the workout's GPX to Garmin as a private course, once per route (a re-push of an
    unchanged route is a no-op; a replaced route deletes the stale course first). Failures are
    recorded in `garmin_course_error` -- the workout itself already succeeded. Only
    `GarminRateLimitAborted` propagates (the whole session is rate limited; stop the loop)."""
    if (
        row.garmin_course_id is not None
        and row.garmin_course_pushed_at is not None
        and row.route_uploaded_at is not None
        and row.route_uploaded_at <= row.garmin_course_pushed_at
    ):
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        storage_path = conn.execute(
            select(raw_object.c.storage_path).where(raw_object.c.id == row.route_raw_object_id)
        ).scalar_one()
        gpx = read_raw_bytes(raw_archive_dir, storage_path)
        course_id = adapter.push_course(
            gpx,
            f"{row.name or 'Run'} {row.local_date} (Perseverer)",
            existing_course_id=row.garmin_course_id,
        )
    except GarminRateLimitAborted:
        raise
    except Exception as e:  # deliberately broad -- a course failure must not fail the workout
        conn.rollback()
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == row.id)
            .values(
                garmin_course_id=None,
                garmin_course_pushed_at=None,
                garmin_course_error=str(e),
                updated_at=now,
            )
        )
        conn.commit()
        return
    conn.execute(
        planned_workout.update()
        .where(planned_workout.c.id == row.id)
        .values(
            garmin_course_id=course_id,
            garmin_course_pushed_at=now,
            garmin_course_error=None,
            updated_at=now,
        )
    )
    conn.commit()


# --- Save (parse + insert/update) -- shared by POST/PUT /planned-workouts and the
# recurring-schedule endpoint, so all three go through one parse-and-store path rather than
# three. --------------------------------------------------------------------------------------


@dataclass
class SavedWorkout:
    id: int
    estimated_duration_s: float
    parse_errors: list[ParseError]


#: Sports with no structured workout-syntax at all -- a name, a duration, and a display-only
#: time of day, nothing parsed (the user's own explicit scoping, see module docstring).
PLACEHOLDER_SPORTS = frozenset({"yoga", "bouldering"})


def _estimate_exercise_duration_s(steps: list[PlannedStepLike]) -> float:
    """Same repeat-expansion shape as `workout_syntax.py`'s own duration estimator, adapted for
    reps-based steps (`_ASSUMED_SECONDS_PER_REP`) alongside time-based ones -- display only, see
    `_build_exercise_step`'s own docstring for why this never reaches Garmin."""
    by_index = {s.step_index: s for s in steps}
    consumed: set[int] = set()
    for s in steps:
        if s.duration_type == "repeat_until_steps_cmplt" and s.repeat_from_step is not None:
            consumed.update(range(s.repeat_from_step, s.step_index))

    def step_estimate(s: PlannedStepLike) -> float:
        if s.duration_type == "reps" and s.duration_reps is not None:
            return s.duration_reps * _ASSUMED_SECONDS_PER_REP
        return s.duration_time_s or 0.0

    total = 0.0
    for s in steps:
        if s.step_index in consumed:
            continue
        if (
            s.duration_type == "repeat_until_steps_cmplt"
            and s.repeat_from_step is not None
            and s.repeat_count is not None
        ):
            children = [
                by_index[i] for i in range(s.repeat_from_step, s.step_index) if i in by_index
            ]
            total += sum(step_estimate(c) for c in children) * s.repeat_count
        else:
            total += step_estimate(s)
    return total


def save_planned_workout(
    conn: Connection,
    *,
    athlete_id: str,
    local_date: str,
    sport: str,
    name: str | None,
    source_text: str | None,
    scheduled_time: str | None = None,
    duration_minutes: float | None = None,
    steps: list[PlannedStepLike] | None = None,
    workout_id: int | None = None,
    comment: str | None = None,
) -> SavedWorkout:
    """Inserts a new `planned_workout` row when `workout_id` is None, or updates that specific
    row in place when given. No longer an upsert-by-date: an athlete can schedule more than one
    workout on the same `local_date`, so create-vs-update is the caller's own decision (the
    router looks the id up and 404s first for PUT, same pattern its delete/push routes already
    use) rather than something this function infers from `(athlete_id, local_date)`. Three sport
    tiers (see module docstring):

    - **running**: `source_text` is the athlete's own workout-syntax text, re-parsed on every
      save (not just the first) into fresh `planned_workout_step` rows -- `duration_minutes`/
      `steps` are ignored, `estimated_duration_s` comes from the parse.
    - **yoga/bouldering** (`PLACEHOLDER_SPORTS`): no syntax to parse at all -- `source_text`, if
      given, is just freeform athlete notes; no `planned_workout_step` rows are ever created;
      `estimated_duration_s` is set directly from `duration_minutes` instead of being derived.
    - **hiit/strength_training** (`EXERCISE_SPORTS`): `steps` are already-structured
      `PlannedStepLike` rows (from the frontend's exercise picker, converted from the API's own
      `PlannedWorkoutStepIn` at the router layer) -- stored as given, no parsing at all;
      `estimated_duration_s` is computed from them (`_estimate_exercise_duration_s`).

    `scheduled_time` ("HH:MM", validated by the API schema layer) is stored either way -- it's
    Perseverer's own calendar display metadata, orthogonal to which sport tier a workout is in.

    Each step in `steps`/`parsed.steps` may carry its own `comment` (running: parsed from an
    inline trailing `# ...` token on that step's own source_text line, workout_syntax.py;
    hiit/strength_training: typed directly against that row in the exercise picker) -- stored
    verbatim per `planned_workout_step` row, never parsed further.

    `comment` (this function's own parameter, distinct from a step's) is a general note for the
    *whole* workout, read before any step -- running/hiit/strength_training only (yoga/bouldering
    already treat `source_text` as freeform notes). Stored verbatim on `planned_workout` itself,
    never parsed; `push_planned_workout` sets it as the pushed Garmin workout's own `description`.

    Resets `push_status` back to "draft" whenever an already-`"pushed"` workout is edited: the
    old Garmin copy is now stale, and `push_planned_workout` re-pushes fresh (delete + re-upload)
    the next time it runs, matching `GarminConnectAdapter.push_planned_workout`'s own "edit means
    full re-push, not a partial update" contract."""
    is_running = sport == "running"
    is_exercise = sport in EXERCISE_SPORTS
    parsed = parse_workout_syntax(source_text or "") if is_running else None

    rows_to_insert: list[Any] = []
    if parsed is not None:
        estimated_duration_s: float | None = parsed.estimated_duration_s
        rows_to_insert = list(parsed.steps)
    elif is_exercise and steps:
        estimated_duration_s = _estimate_exercise_duration_s(steps)
        rows_to_insert = list(steps)
    else:
        estimated_duration_s = duration_minutes * 60.0 if duration_minutes is not None else None

    now = datetime.now(UTC).replace(tzinfo=None)

    if workout_id is None:
        result = conn.execute(
            planned_workout.insert().values(
                athlete_id=athlete_id,
                local_date=local_date,
                sport=sport,
                name=name,
                source_text=source_text,
                estimated_duration_s=estimated_duration_s,
                scheduled_time=scheduled_time,
                comment=comment,
                push_status="draft",
                created_at=now,
                updated_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        workout_id = result.inserted_primary_key[0]
        assert isinstance(workout_id, int)
    else:
        # The router already resolved+404'd this id before calling in (same pattern its
        # delete/push routes use) -- this select is just for the current push_status, not an
        # existence check of our own.
        existing = conn.execute(
            select(planned_workout.c.push_status).where(planned_workout.c.id == workout_id)
        ).fetchone()
        assert existing is not None
        new_status = "draft" if existing.push_status == "pushed" else existing.push_status
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == workout_id)
            .values(
                sport=sport,
                name=name,
                source_text=source_text,
                estimated_duration_s=estimated_duration_s,
                scheduled_time=scheduled_time,
                comment=comment,
                push_status=new_status,
                updated_at=now,
            )
        )
        conn.execute(
            planned_workout_step.delete().where(
                planned_workout_step.c.planned_workout_id == workout_id
            )
        )

    if rows_to_insert:
        conn.execute(
            planned_workout_step.insert(),
            [
                {
                    "athlete_id": athlete_id,
                    "planned_workout_id": workout_id,
                    "step_index": s.step_index,
                    "duration_type": s.duration_type,
                    "duration_time_s": s.duration_time_s,
                    "duration_distance_m": getattr(s, "duration_distance_m", None),
                    "duration_reps": getattr(s, "duration_reps", None),
                    "target_type": getattr(s, "target_type", None),
                    "target_low": getattr(s, "target_low", None),
                    "target_high": getattr(s, "target_high", None),
                    "target_hr_zone": getattr(s, "target_hr_zone", None),
                    "cadence_low": getattr(s, "cadence_low", None),
                    "cadence_high": getattr(s, "cadence_high", None),
                    "intensity": s.intensity,
                    "repeat_from_step": s.repeat_from_step,
                    "repeat_count": s.repeat_count,
                    "exercise_category": getattr(s, "exercise_category", None),
                    "exercise_name": getattr(s, "exercise_name", None),
                    "weight_kg": getattr(s, "weight_kg", None),
                    "comment": getattr(s, "comment", None),
                }
                # rows_to_insert holds either workout_syntax.ParsedStep (running) or
                # PlannedStepLike (hiit/strength_training) -- getattr() above covers whichever
                # fields the other type doesn't have, rather than a second near-identical insert
                # block per type.
                for s in rows_to_insert
            ],
        )

    return SavedWorkout(
        id=workout_id,
        estimated_duration_s=estimated_duration_s or 0.0,
        parse_errors=parsed.errors if parsed is not None else [],
    )


# --- Recurrence date-math -- POST /planned-workouts/recurring -------------------------------

_MAX_RECURRENCE_OCCURRENCES = 366  # a safety cap, not a real product limit -- prevents a typo'd
# "until" a decade out from silently generating thousands of rows.


def _add_calendar_month(d: date) -> date:
    """One calendar month later, clamping the day to the target month's own length (Jan 31 ->
    Feb 28/29) -- there is no "the 31st" in February, and silently rolling over into March would
    violate the athlete's own "same day each month" intent worse than clamping does."""
    month = d.month + 1
    year = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def compute_recurrence_dates(
    start: date,
    frequency: str,
    *,
    interval_days: int | None = None,
    count: int | None = None,
    until: date | None = None,
) -> list[date]:
    """`start` plus every following occurrence date, ascending, `start` itself always first --
    "every week" / "every N days" / "every month", for `count` total occurrences (including
    `start`) or up to and including `until`, whichever the caller supplies (exactly one of the
    two, never both/neither -- an unambiguous stop condition, not a guess). Capped at
    `_MAX_RECURRENCE_OCCURRENCES` regardless, as a safety net against a mistaken far-future
    `until`."""
    if (count is None) == (until is None):
        raise ValueError("supply exactly one of count or until")
    if frequency == "every_n_days" and (interval_days is None or interval_days < 1):
        raise ValueError("interval_days must be >= 1 for frequency='every_n_days'")
    if frequency not in ("weekly", "every_n_days", "monthly"):
        raise ValueError(f"unknown frequency {frequency!r}")

    dates = [start]
    current = start
    while True:
        if count is not None and len(dates) >= count:
            break
        if len(dates) >= _MAX_RECURRENCE_OCCURRENCES:
            break
        if frequency == "weekly":
            current = current + timedelta(days=7)
        elif frequency == "every_n_days":
            assert interval_days is not None
            current = current + timedelta(days=interval_days)
        else:  # monthly
            current = _add_calendar_month(current)
        if until is not None and current > until:
            break
        dates.append(current)
    return dates


# --- Matching against recorded activities -- a read-time-only signal for the Week view's
# compliance stat and the Day/Month view's own "Done" indicator, alongside (never replacing) the
# athlete's own explicit `completed_at` marker. -----------------------------------------------


def activity_matches_planned_sport(
    sport: str, activity_sport: str, activity_sub_sport: str | None
) -> bool:
    """Whether a recorded activity's own (sport, sub_sport) plausibly satisfies a
    planned_workout's sport tier -- not a literal sport==sport check, since several tiers are
    recorded under FIT's generic "training" container sport with the real discipline only in
    sub_sport (garmin_activity_summary.py's own GARMIN_ACTIVITY_TYPE_MAP: yoga -> (training,
    yoga), strength_training -> (training, strength_training); rock_climbing/bouldering the same
    shape). running reuses merge/engine.py's own sport_family() so a trail/treadmill/track run
    still satisfies a plain "running" plan, the same leniency this app's own merge-matching
    already applies. hiit/strength_training each accept two real shapes -- confirmed against
    this project's own sport taxonomy: a literal top-level sport ("hiit", or family "strength"
    for "strength_training") *or* the "training" container with a matching sub_sport, since
    real activities of both shapes exist in this codebase's own data."""
    if sport == "running":
        return sport_family(activity_sport) == "run"
    if sport == "yoga":
        return activity_sport == "training" and activity_sub_sport == "yoga"
    if sport == "bouldering":
        return activity_sport == "rock_climbing" and activity_sub_sport == "bouldering"
    if sport == "hiit":
        return activity_sport == "hiit" or (
            activity_sport == "training" and activity_sub_sport == "hiit"
        )
    if sport == "strength_training":
        return sport_family(activity_sport) == "strength" or (
            activity_sport == "training" and activity_sub_sport == "strength_training"
        )
    return False


def activities_by_local_date(
    conn: Connection, athlete_id: str, start_date: str, end_date: str
) -> dict[str, list[tuple[str, str, str | None]]]:
    """One query for a whole date range's worth of recorded activities -- `(activity_id, sport,
    sub_sport)` tuples grouped by `local_date` -- so matching every planned workout in that range
    against them costs one query total, not one per workout (the same "fetch once, match in
    Python" precedent race_readiness.py's own weekly queries already establish)."""
    rows = conn.execute(
        select(activity.c.local_date, activity.c.id, activity.c.sport, activity.c.sub_sport)
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.local_date >= start_date,
            activity.c.local_date <= end_date,
        )
        .order_by(activity.c.start_time_utc)
    ).fetchall()
    by_date: dict[str, list[tuple[str, str, str | None]]] = {}
    for row in rows:
        by_date.setdefault(row.local_date, []).append((row.id, row.sport, row.sub_sport))
    return by_date


def matching_activity_id(
    activities_that_day: list[tuple[str, str, str | None]], sport: str
) -> str | None:
    """The first (earliest-starting, since `activities_by_local_date` orders by start time)
    recorded activity that day satisfying this planned workout's sport tier, or `None` -- a
    plain yes/no signal, so which one wins when more than one plausibly matches doesn't otherwise
    matter."""
    for activity_id, activity_sport, activity_sub_sport in activities_that_day:
        if activity_matches_planned_sport(sport, activity_sport, activity_sub_sport):
            return activity_id
    return None
