"""Request/response models for the coach endpoints (routers/coach.py)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CoachPersonOut(BaseModel):
    """Someone on the other side of a coaching link."""

    id: str
    display_name: str
    username: str | None
    since: datetime


class CoachOut(BaseModel):
    coach: CoachPersonOut | None
    coachees: list[CoachPersonOut]


class CoachIn(BaseModel):
    username: str = Field(min_length=1)
