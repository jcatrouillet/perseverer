"""Request/response models for /planned-workouts -- scheduled (future) workouts authored on the
calendar and pushed to the Garmin watch. See db/schema.py::planned_workout for the storage shape,
workout_syntax.py for the text syntax, and planned_workouts.py for the push/save orchestration.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

_SCHEDULED_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_scheduled_time(v: str | None) -> str | None:
    if v is not None and not _SCHEDULED_TIME_RE.match(v):
        raise ValueError('scheduled_time must be "HH:MM" (24h), e.g. "18:30"')
    return v


class PlannedWorkoutStepOut(BaseModel):
    step_index: int
    duration_type: str | None
    duration_time_s: float | None
    duration_distance_m: float | None
    target_type: str | None
    # Unit depends on target_type: m/s for "pace", bpm for "heart_rate" -- see
    # db/schema.py::planned_workout_step's own docstring.
    target_low: float | None
    target_high: float | None
    target_hr_zone: int | None
    cadence_low: int | None
    cadence_high: int | None
    intensity: str | None
    repeat_from_step: int | None
    repeat_count: int | None
    # hiit/strength_training only (EXERCISE_SPORTS, planned_workouts.py) -- a specific Garmin
    # exercise picked from garminconnect.exercises' catalog, plus a rep count in place of
    # duration_time_s/duration_distance_m (duration_type == "reps").
    duration_reps: int | None = None
    exercise_category: str | None = None
    exercise_name: str | None = None
    weight_kg: float | None = None
    # A freeform note on this specific step -- running: parsed from an inline trailing
    # "# comment" token on that step's own source_text line (workout_syntax.py). hiit/
    # strength_training: typed directly against that row in the exercise picker. Never parsed
    # further, never sent to Garmin.
    comment: str | None = None


class PlannedWorkoutStepIn(BaseModel):
    """One step of a hiit/strength_training workout, as authored by the frontend's exercise
    picker (EXERCISE_SPORTS, planned_workouts.py) -- never parsed from text, unlike running's
    source_text. Converted to a `planned_workouts.PlannedStepLike` at the router layer."""

    step_index: int
    # "reps" (a counted set, e.g. "10 reps of bench press") | "time" (e.g. a plank hold) | "rest"
    duration_type: str
    duration_time_s: float | None = None
    duration_reps: int | None = None
    intensity: str | None = None  # "active" | "rest" -- same vocabulary every other tier uses
    repeat_from_step: int | None = None
    repeat_count: int | None = None
    # The exact (category, exercise) pair from garminconnect.exercises, e.g.
    # ("BENCH_PRESS", "") or ("CURL", "HAMMER_CURL") -- exercise_name is "" (not None) when the
    # step names just the category with no specific variant, matching Garmin's own convention
    # (see db/schema.py::planned_workout_step's own docstring). Absent entirely for a rest step.
    exercise_category: str | None = None
    exercise_name: str | None = None
    weight_kg: float | None = None
    # See PlannedWorkoutStepOut above -- a per-step freeform note, hiit/strength_training typed
    # directly, running ignores this on the *In shape (comes from parsing source_text instead).
    comment: str | None = None


class ParseErrorOut(BaseModel):
    line_no: int
    message: str


class PlannedWorkoutIn(BaseModel):
    sport: str  # "running" | "yoga" | "bouldering" | "hiit" | "strength_training" -- open string,
    # not an enum, but "fitness" was dropped from the frontend's own sport list (no structured
    # syntax and no placeholder builder ever existed for it) rather than kept as a dead option.
    name: str | None = None
    # running: the athlete's own workout-syntax text. yoga/bouldering (PLACEHOLDER_SPORTS,
    # planned_workouts.py): freeform notes only, never parsed -- no structured syntax for these.
    # hiit/strength_training: ignored -- steps below carries the structured content instead.
    source_text: str | None = None
    # "HH:MM", 24h -- Perseverer's own calendar display metadata only (Garmin's own
    # schedule_workout() has no time-of-day API at all, see docs/adr/0015-scheduled-workouts.md).
    scheduled_time: str | None = None
    # yoga/bouldering only: sets estimated_duration_s directly (there's no syntax to derive a
    # duration from). Ignored for running/hiit/strength_training, where estimated_duration_s is
    # derived instead (from source_text or steps respectively).
    duration_minutes: float | None = None
    # hiit/strength_training only (EXERCISE_SPORTS) -- the exercise-picker steps. Ignored for
    # every other sport.
    steps: list[PlannedWorkoutStepIn] | None = None

    _validate_scheduled_time = field_validator("scheduled_time")(_validate_scheduled_time)


class PlannedWorkoutOut(BaseModel):
    # False whenever no workout is scheduled for this date yet -- every field below is null/
    # empty in that case, same "no-404-for-absence" idiom GET /goals already uses.
    available: bool
    id: int | None = None
    local_date: str | None = None
    sport: str | None = None
    name: str | None = None
    source_text: str | None = None
    scheduled_time: str | None = None
    estimated_duration_s: float | None = None
    steps: list[PlannedWorkoutStepOut] = []
    # Parse errors from the *currently stored* source_text -- surfaced so the schedule form can
    # underline a malformed line even after a reload, not just live as the athlete types.
    parse_errors: list[ParseErrorOut] = []
    push_status: str | None = None  # "draft" | "pushed" | "push_failed"
    push_error: str | None = None
    garmin_workout_id: int | None = None
    garmin_scheduled_at: str | None = None


class PlannedWorkoutListItemOut(BaseModel):
    """One row of GET /planned-workouts?start_date=&end_date= -- just enough for the calendar
    grid's own per-day indicator (mirrors day_rollup's own summary-row shape for GET /calendar);
    fetch GET /planned-workouts/{date} for the full workout once a day is expanded."""

    local_date: str
    id: int
    sport: str
    name: str | None
    scheduled_time: str | None
    push_status: str


class RecurringWorkoutIn(BaseModel):
    local_date: str  # first occurrence -- ISO date
    sport: str
    name: str | None = None
    source_text: str | None = None
    scheduled_time: str | None = None
    duration_minutes: float | None = None  # yoga/bouldering only, see PlannedWorkoutIn
    # hiit/strength_training only, see PlannedWorkoutIn
    steps: list[PlannedWorkoutStepIn] | None = None
    frequency: str  # "weekly" | "every_n_days" | "monthly"
    interval_days: int | None = None  # required (>=1) when frequency == "every_n_days"
    # Exactly one of count/until -- an unambiguous stop condition, not a guess. `count` includes
    # the first occurrence itself.
    count: int | None = None
    until: str | None = None  # ISO date, inclusive

    _validate_scheduled_time = field_validator("scheduled_time")(_validate_scheduled_time)


class RecurringWorkoutOut(BaseModel):
    created_dates: list[str]
    # A date that already had a planned workout is skipped, not overwritten and not an error --
    # see planned_workouts.py::compute_recurrence_dates' own docstring / the router's own
    # handling.
    skipped_dates: list[str]
