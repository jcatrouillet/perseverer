"""Request/response shapes for GET/PUT /settings/commute (home and work addresses)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CommuteSettingsIn(BaseModel):
    # What the athlete typed; empty or null clears it. Looked up (once, when it changes) to find
    # the point it refers to.
    home_address: str | None = Field(default=None, max_length=300)
    work_address: str | None = Field(default=None, max_length=300)


class CommuteSettingsOut(BaseModel):
    home_address: str | None
    home_label: str | None  # what the address was resolved to, for the athlete to check
    work_address: str | None
    work_label: str | None
    # Detection runs only when both are set.
    detection_active: bool
    # Activities currently tagged as a commute by detection (not by hand).
    detected_count: int
    radius_m: float
