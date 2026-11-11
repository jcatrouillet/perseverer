"""Request/response models for the handwritten-notes feature of a HIIT / strength activity: photos
of the athlete's notes, what Claude read from them, and the sets the athlete confirmed. See
db/schema.py::activity_photo / activity_logged_set and handwriting.py. SI units: kg.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from perseverer.handwriting import DecodedLogbook


class PhotoOut(BaseModel):
    id: int
    content_type: str
    byte_size: int
    created_at: datetime
    # null: the athlete has not asked for the handwriting to be read.
    decode_status: Literal["pending", "done", "failed"] | None
    decode_error: str | None
    decoded: DecodedLogbook | None  # what was read: a suggestion to correct, never stored as sets
    decoded_at: datetime | None


class LogbookStatusOut(BaseModel):
    """Whether reading a photo is possible on this server (an API key is configured)."""

    can_read_handwriting: bool


class LoggedSetIn(BaseModel):
    reps: int | None = Field(default=None, ge=0, le=10000)
    weight_kg: float | None = Field(default=None, ge=0, le=10000)
    duration_s: float | None = Field(default=None, ge=0, le=86400)


class LoggedExerciseIn(BaseModel):
    """One exercise as the athlete wrote it, matched to a library exercise when they chose one."""

    label: str = Field(min_length=1, max_length=200)
    exercise_category: str | None = Field(default=None, max_length=100)
    exercise_name: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)
    photo_id: int | None = None
    sets: list[LoggedSetIn] = Field(max_length=200)


class LogbookIn(BaseModel):
    """The whole logbook of an activity, exercises in the order they were done. Replaces what was
    saved before; an empty list clears it."""

    exercises: list[LoggedExerciseIn] = Field(max_length=100)


class LoggedExerciseOut(LoggedExerciseIn):
    pass


class LogbookOut(BaseModel):
    exercises: list[LoggedExerciseOut]
