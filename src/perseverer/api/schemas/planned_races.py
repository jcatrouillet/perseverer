"""Request/response models for /planned-races -- a single upcoming race on the calendar. See
db/schema.py::planned_race for the storage shape and planned_races.py for the prediction lookup.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, field_validator, model_validator

# Same "HH:MM" (24h) convention/regex as planned_workouts.py's own scheduled_time -- duplicated
# rather than imported (that module's own validator is private to it, and this codebase's own
# precedent is to duplicate a small helper like this across semantically-separate domains rather
# than cross-import a leading-underscore name -- see calendar_feed.py's token-helper docstring).
_SCHEDULED_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class PlannedRaceIn(BaseModel):
    local_date: str  # ISO date
    name: str
    sport: str = "running"  # open string, default running -- same convention as planned_workout
    distance_m: float
    scheduled_time: str | None = None  # "HH:MM", 24h -- optional, display-only
    target_duration_s: float | None = None  # the athlete's own goal finish time; None = no target

    @field_validator("scheduled_time")
    @classmethod
    def _valid_scheduled_time(cls, v: str | None) -> str | None:
        if v is not None and not _SCHEDULED_TIME_RE.match(v):
            raise ValueError('scheduled_time must be "HH:MM" (24h), e.g. "09:00"')
        return v

    @field_validator("local_date")
    @classmethod
    def _valid_iso_date(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError as e:
            raise ValueError("local_date must be an ISO date (YYYY-MM-DD)") from e
        return v

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v

    @model_validator(mode="after")
    def _positive_values(self) -> PlannedRaceIn:
        if self.distance_m <= 0:
            raise ValueError("distance_m must be positive")
        if self.target_duration_s is not None and self.target_duration_s <= 0:
            raise ValueError("target_duration_s must be positive")
        return self


class PlannedRaceOut(BaseModel):
    id: int
    local_date: str
    name: str
    sport: str
    distance_m: float
    scheduled_time: str | None
    target_duration_s: float | None
    # Read-only, computed at request time (planned_races.py) -- never stored.
    days_until: int
    predicted_duration_s: float | None
