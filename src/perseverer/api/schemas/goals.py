"""Request/response models for GET/PUT/DELETE /goals -- a distance goal for a whole calendar
year, month or 7-day week, and the progress line computed against it. See db/schema.py::goal for the
storage shape and goals.py for the progress computation.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator


class GoalIn(BaseModel):
    period_type: str  # "week" | "month" | "year"
    period_start: str  # "YYYY" year, "YYYY-MM" month, or the ISO date a 7-day week starts on
    sport: str | None = None  # None = every sport combined
    target_distance_m: float

    @field_validator("period_type")
    @classmethod
    def _valid_period_type(cls, v: str) -> str:
        if v not in ("week", "month", "year"):
            raise ValueError('period_type must be "week", "month" or "year"')
        return v

    @field_validator("target_distance_m")
    @classmethod
    def _positive_target(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("target_distance_m must be greater than zero")
        return v


class GoalRepeatIn(GoalIn):
    """A weekly goal repeated over `weeks` consecutive weeks starting at `period_start` (which is
    the first week's own start date, so `period_type` must be "week")."""

    weeks: int

    @field_validator("weeks")
    @classmethod
    def _sane_weeks(cls, v: int) -> int:
        if not 1 <= v <= 104:
            raise ValueError("weeks must be between 1 and 104")
        return v

    @model_validator(mode="after")
    def _weekly_only(self) -> GoalRepeatIn:
        if self.period_type != "week":
            raise ValueError('only a "week" goal can be repeated')
        return self


class GoalOut(BaseModel):
    id: int
    period_type: str
    period_start: str
    sport: str | None
    target_distance_m: float


class GoalProgressPoint(BaseModel):
    local_date: str
    cumulative_distance_m: float


class GoalProgressOut(BaseModel):
    # False whenever no goal is set for this period yet -- every field below is null/empty in
    # that case, never a fabricated placeholder progress line.
    available: bool
    goal: GoalOut | None = None
    period_end: str | None = None
    daily: list[GoalProgressPoint] = []
    target_per_day_m: float | None = None
    current_distance_m: float | None = None
    target_distance_as_of_today_m: float | None = None
    ahead_behind_m: float | None = None
    pct_complete: float | None = None


class GoalRepeatOut(BaseModel):
    goals: list[GoalOut]
