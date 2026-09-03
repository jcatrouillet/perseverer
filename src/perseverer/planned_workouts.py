"""Turns a saved `planned_workout`'s steps into a real Garmin `RunningWorkout` and runs the push
-- see `db/schema.py`'s own docstring for the storage shape and `workout_syntax.py` for how the
athlete's typed `source_text` becomes `planned_workout_step` rows in the first place.

Kept separate from `api/routers/planned_workouts.py` (plain CRUD stays inline there, the same
balance `goals.py`/`api/routers/goals.py` already strikes -- a thin request/response router, a
service module for anything that touches more than one table or an external system) because
building the actual Garmin workout JSON needs `hr_zones.py` (to resolve a "Z2 HR" step target
against the athlete's own configured zones) and the adapter's push path, more than a router
layer should carry.

Running only, v1 (`push_planned_workout`'s own "running first" scope, see
docs/adr/0015-scheduled-workouts.md) -- yoga/bouldering/fitness are simpler and will reuse this
same module's push orchestration once they're built, but have no step-level structure to turn
into a `RunningWorkout` yet.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from garminconnect import Garmin
from garminconnect.workout import (
    ConditionType,
    ExecutableStep,
    RepeatGroup,
    RunningWorkout,
    SportType,
    StepType,
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
from perseverer.db.schema import athlete_hr_zone_config, planned_workout, planned_workout_step
from perseverer.hr_zones import compute_hr_zone_boundaries, resolve_hr_zone_bpm
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


_PLANNED_STEP_FIELD_NAMES = [f.name for f in fields(PlannedStepLike)]


def _step_type_dict(intensity: str | None) -> dict[str, Any]:
    step_type_id, key, display_order = _INTENSITY_TO_STEP_TYPE.get(
        intensity or "", _DEFAULT_STEP_TYPE
    )
    return {"stepTypeId": step_type_id, "stepTypeKey": key, "displayOrder": display_order}


def _end_condition(step: PlannedStepLike) -> tuple[dict[str, Any], float]:
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
    zoneNumber). Web-confirmed wire format (see docs/adr/0015-scheduled-workouts.md's own
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
    """"Ride cadence alongside the primary pace/HR target" via a `secondaryTargetType`/
    `secondaryTargetValueOne`/`secondaryTargetValueTwo` triple -- **live-verified** (2026-09-03,
    a real push + `get_workout_by_id` read-back against the athlete's own Garmin account, see
    docs/adr/0015-scheduled-workouts.md's Verification section): Garmin's server accepts and
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


def build_workout_segment(
    steps: list[PlannedStepLike],
    *,
    hr_boundaries: tuple[int, int, int, int] | None,
    max_hr_bpm: float | None,
) -> list[ExecutableStep | RepeatGroup]:
    """The unexpanded `planned_workout_step` rows (repeat children preceding their own summary
    row -- see module docstring) into Garmin's own `ExecutableStep`/`RepeatGroup` tree.
    `stepOrder` is one flat, ascending counter across the whole segment, matching the reference
    `create_strength_set` helper's own convention: a repeat group's `stepOrder` comes *before*
    its children's (group=N, children=N+1, N+2, ...), not after."""
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
                children.append(
                    _build_executable_step(
                        child, counter, hr_boundaries=hr_boundaries, max_hr_bpm=max_hr_bpm
                    )
                )
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
            segment_steps.append(
                _build_executable_step(
                    s, counter, hr_boundaries=hr_boundaries, max_hr_bpm=max_hr_bpm
                )
            )
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
    segment_steps = build_workout_segment(steps, hr_boundaries=hr_boundaries, max_hr_bpm=max_hr_bpm)
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
) -> PushResult:
    """Loads one `planned_workout` + its steps, builds the Garmin `RunningWorkout`, and pushes
    it -- called from both `POST /planned-workouts/{date}/push` (manual, one workout) and the
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
    """
    row = conn.execute(
        select(planned_workout).where(
            planned_workout.c.id == planned_workout_id, planned_workout.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        raise ValueError(f"planned_workout {planned_workout_id} not found")

    now = datetime.now(UTC).replace(tzinfo=None)

    def _mark_failed(message: str) -> PushResult:
        conn.rollback()
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == planned_workout_id)
            .values(push_status="push_failed", push_error=message, updated_at=now)
        )
        conn.commit()
        return PushResult(success=False, garmin_workout_id=None, error=message)

    if row.sport != "running":
        return _mark_failed(f"push not supported yet for sport {row.sport!r} (running only, v1)")

    try:
        step_rows = conn.execute(
            select(planned_workout_step)
            .where(planned_workout_step.c.planned_workout_id == planned_workout_id)
            .order_by(planned_workout_step.c.step_index)
        ).fetchall()
        steps = [
            PlannedStepLike(**{name: getattr(r, name) for name in _PLANNED_STEP_FIELD_NAMES})
            for r in step_rows
        ]

        zone_row = conn.execute(
            select(athlete_hr_zone_config).where(athlete_hr_zone_config.c.athlete_id == athlete_id)
        ).fetchone()
        hr_boundaries = None
        max_hr_bpm = None
        if zone_row is not None:
            hr_boundaries = compute_hr_zone_boundaries(
                zone_row.max_hr_bpm, zone_row.threshold_hr_bpm, zone_row.resting_hr_bpm
            )
            max_hr_bpm = zone_row.max_hr_bpm

        workout = build_running_workout(
            row.name or f"Run - {row.local_date}",
            steps,
            row.estimated_duration_s or 0.0,
            hr_boundaries=hr_boundaries,
            max_hr_bpm=max_hr_bpm,
        )

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
    return PushResult(success=True, garmin_workout_id=workout_id, error=None)


# --- Save (parse + upsert) -- shared by PUT /planned-workouts/{date} and the recurring-schedule
# endpoint, so both go through one parse-and-store path rather than two. --------------------


@dataclass
class SavedWorkout:
    id: int
    estimated_duration_s: float
    parse_errors: list[ParseError]


def save_planned_workout(
    conn: Connection,
    *,
    athlete_id: str,
    local_date: str,
    sport: str,
    name: str | None,
    source_text: str | None,
) -> SavedWorkout:
    """Upserts one `planned_workout` row by (athlete_id, local_date) and replaces its
    `planned_workout_step` rows from a fresh parse of `source_text` -- `source_text` is
    re-parsed on every save, not just the first (see `db/schema.py::planned_workout`'s own
    docstring). Resets `push_status` back to "draft" whenever an already-`"pushed"` workout is
    edited: the old Garmin copy is now stale, and `push_planned_workout` re-pushes fresh (delete
    + re-upload) the next time it runs, matching `GarminConnectAdapter.push_planned_workout`'s
    own "edit means full re-push, not a partial update" contract."""
    parsed = parse_workout_syntax(source_text or "")
    now = datetime.now(UTC).replace(tzinfo=None)

    existing = conn.execute(
        select(planned_workout.c.id, planned_workout.c.push_status).where(
            planned_workout.c.athlete_id == athlete_id, planned_workout.c.local_date == local_date
        )
    ).fetchone()

    if existing is None:
        result = conn.execute(
            planned_workout.insert().values(
                athlete_id=athlete_id,
                local_date=local_date,
                sport=sport,
                name=name,
                source_text=source_text,
                estimated_duration_s=parsed.estimated_duration_s,
                push_status="draft",
                created_at=now,
                updated_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        workout_id = result.inserted_primary_key[0]
        assert isinstance(workout_id, int)
    else:
        workout_id = existing.id
        new_status = "draft" if existing.push_status == "pushed" else existing.push_status
        conn.execute(
            planned_workout.update()
            .where(planned_workout.c.id == workout_id)
            .values(
                sport=sport,
                name=name,
                source_text=source_text,
                estimated_duration_s=parsed.estimated_duration_s,
                push_status=new_status,
                updated_at=now,
            )
        )
        conn.execute(
            planned_workout_step.delete().where(
                planned_workout_step.c.planned_workout_id == workout_id
            )
        )

    if parsed.steps:
        conn.execute(
            planned_workout_step.insert(),
            [
                {
                    "athlete_id": athlete_id,
                    "planned_workout_id": workout_id,
                    "step_index": s.step_index,
                    "duration_type": s.duration_type,
                    "duration_time_s": s.duration_time_s,
                    "duration_distance_m": s.duration_distance_m,
                    "target_type": s.target_type,
                    "target_low": s.target_low,
                    "target_high": s.target_high,
                    "target_hr_zone": s.target_hr_zone,
                    "cadence_low": s.cadence_low,
                    "cadence_high": s.cadence_high,
                    "intensity": s.intensity,
                    "repeat_from_step": s.repeat_from_step,
                    "repeat_count": s.repeat_count,
                }
                for s in parsed.steps
            ],
        )

    return SavedWorkout(
        id=workout_id,
        estimated_duration_s=parsed.estimated_duration_s,
        parse_errors=parsed.errors,
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
