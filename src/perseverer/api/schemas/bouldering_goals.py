"""Request/response models for /bouldering-goals -- a target number of completed bouldering routes
in a week, month or year, at a specific V-grade (optionally "or harder") or any grade. See
db/schema.py::bouldering_goal for storage and bouldering_goals.py for the progress computation.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator


class BoulderingGoalIn(BaseModel):
    period_type: str  # "week" | "month" | "year"
    period_start: str  # "YYYY" year, "YYYY-MM" month, or the ISO date a 7-day week starts on
    grade: int | None = None  # V-grade; None = routes of any grade
    and_harder: bool = False  # only meaningful with a grade
    target_count: int

    @field_validator("period_type")
    @classmethod
    def _valid_period_type(cls, v: str) -> str:
        if v not in ("week", "month", "year"):
            raise ValueError('period_type must be "week", "month" or "year"')
        return v

    @field_validator("grade")
    @classmethod
    def _valid_grade(cls, v: int | None) -> int | None:
        if v is not None and not 0 <= v <= 20:
            raise ValueError("grade must be a V-grade between 0 and 20")
        return v

    @field_validator("target_count")
    @classmethod
    def _positive_target(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("target_count must be greater than zero")
        return v

    @model_validator(mode="after")
    def _harder_needs_grade(self) -> BoulderingGoalIn:
        if self.and_harder and self.grade is None:
            raise ValueError("and_harder needs a grade")
        return self


class BoulderingGoalRepeatIn(BoulderingGoalIn):
    """A weekly bouldering goal repeated over `weeks` consecutive weeks starting at `period_start`
    (the first week's own start date, so `period_type` must be "week")."""

    weeks: int

    @field_validator("weeks")
    @classmethod
    def _sane_weeks(cls, v: int) -> int:
        if not 1 <= v <= 104:
            raise ValueError("weeks must be between 1 and 104")
        return v

    @model_validator(mode="after")
    def _weekly_only(self) -> BoulderingGoalRepeatIn:
        if self.period_type != "week":
            raise ValueError('only a "week" goal can be repeated')
        return self


class BoulderingGoalOut(BaseModel):
    id: int
    period_type: str
    period_start: str
    grade: int | None
    and_harder: bool
    target_count: int


class BoulderingGoalProgressPoint(BaseModel):
    local_date: str
    cumulative_count: int


class BoulderingGoalProgressOut(BaseModel):
    goal: BoulderingGoalOut
    period_end: str
    daily: list[BoulderingGoalProgressPoint]
    target_per_day: float
    current_count: int
    target_as_of_today: float
    ahead_behind: float
    pct_complete: float


class BoulderingGoalRepeatOut(BaseModel):
    created: list[BoulderingGoalOut]
    # Weeks that already held an identical goal (same grade and "or harder"), left as they were.
    skipped_period_starts: list[str]
