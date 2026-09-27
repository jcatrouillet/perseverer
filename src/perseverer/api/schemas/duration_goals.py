"""Request/response models for /duration-goals -- a target amount of time on one sport (or every
sport combined) in a week, month or year. See db/schema.py::duration_goal for storage and
duration_goals.py for the progress computation.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator


class DurationGoalIn(BaseModel):
    period_type: str  # "week" | "month" | "year"
    period_start: str  # "YYYY" year, "YYYY-MM" month, or the ISO date a 7-day week starts on
    sport: str | None = None  # None = every sport combined
    target_duration_s: float

    @field_validator("period_type")
    @classmethod
    def _valid_period_type(cls, v: str) -> str:
        if v not in ("week", "month", "year"):
            raise ValueError('period_type must be "week", "month" or "year"')
        return v

    @field_validator("target_duration_s")
    @classmethod
    def _positive_target(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("target_duration_s must be greater than zero")
        return v


class DurationGoalRepeatIn(DurationGoalIn):
    """A weekly duration goal repeated over `weeks` consecutive weeks starting at `period_start`
    (the first week's own start date, so `period_type` must be "week")."""

    weeks: int

    @field_validator("weeks")
    @classmethod
    def _sane_weeks(cls, v: int) -> int:
        if not 1 <= v <= 104:
            raise ValueError("weeks must be between 1 and 104")
        return v

    @model_validator(mode="after")
    def _weekly_only(self) -> DurationGoalRepeatIn:
        if self.period_type != "week":
            raise ValueError('only a "week" goal can be repeated')
        return self


class DurationGoalOut(BaseModel):
    id: int
    period_type: str
    period_start: str
    sport: str | None
    target_duration_s: float


class DurationGoalRepeatOut(BaseModel):
    created: list[DurationGoalOut]
    # Weeks that already held a goal for the same sport, left as they were.
    skipped_period_starts: list[str]


class DurationGoalProgressPoint(BaseModel):
    local_date: str
    cumulative_duration_s: float


class DurationGoalProgressOut(BaseModel):
    goal: DurationGoalOut
    period_end: str
    daily: list[DurationGoalProgressPoint]
    target_per_day_s: float
    current_duration_s: float
    target_as_of_today_s: float
    ahead_behind_s: float
    pct_complete: float
