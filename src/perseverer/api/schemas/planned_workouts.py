"""Request/response models for /planned-workouts -- scheduled (future) workouts authored on the
calendar and pushed to the Garmin watch. See db/schema.py::planned_workout for the storage shape,
workout_syntax.py for the text syntax, and planned_workouts.py for the push/save orchestration.
"""

from __future__ import annotations

from pydantic import BaseModel


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


class ParseErrorOut(BaseModel):
    line_no: int
    message: str


class PlannedWorkoutIn(BaseModel):
    sport: str  # "running" | "yoga" | "bouldering" | "fitness" -- open string, not an enum
    name: str | None = None
    source_text: str | None = None


class PlannedWorkoutOut(BaseModel):
    # False whenever no workout is scheduled for this date yet -- every field below is null/
    # empty in that case, same "no-404-for-absence" idiom GET /goals already uses.
    available: bool
    id: int | None = None
    local_date: str | None = None
    sport: str | None = None
    name: str | None = None
    source_text: str | None = None
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
    push_status: str


class RecurringWorkoutIn(BaseModel):
    local_date: str  # first occurrence -- ISO date
    sport: str
    name: str | None = None
    source_text: str | None = None
    frequency: str  # "weekly" | "every_n_days" | "monthly"
    interval_days: int | None = None  # required (>=1) when frequency == "every_n_days"
    # Exactly one of count/until -- an unambiguous stop condition, not a guess. `count` includes
    # the first occurrence itself.
    count: int | None = None
    until: str | None = None  # ISO date, inclusive


class RecurringWorkoutOut(BaseModel):
    created_dates: list[str]
    # A date that already had a planned workout is skipped, not overwritten and not an error --
    # see planned_workouts.py::compute_recurrence_dates' own docstring / the router's own
    # handling.
    skipped_dates: list[str]
