"""Request/response models for GET/PUT/DELETE /goals -- a distance goal for a whole calendar
year or month, and the progress line computed against it. See db/schema.py::goal for the
storage shape and goals.py for the progress computation.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class GoalIn(BaseModel):
    period_type: str  # "year" | "month"
    period_start: str  # "YYYY" for a year, "YYYY-MM" for a month
    sport: str | None = None  # None = every sport combined
    target_distance_m: float

    @field_validator("period_type")
    @classmethod
    def _valid_period_type(cls, v: str) -> str:
        if v not in ("year", "month"):
            raise ValueError('period_type must be "year" or "month"')
        return v

    @field_validator("target_distance_m")
    @classmethod
    def _positive_target(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("target_distance_m must be greater than zero")
        return v


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
