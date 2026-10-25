"""Request/response shapes for /posters (year posters rendered in the background)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PosterIn(BaseModel):
    kind: Literal["grid", "heatmap", "calendar", "circular"]
    year: int = Field(ge=1990, le=2100)
    # Empty = every sport.
    sports: list[str] = Field(default_factory=list, max_length=40)
    # Virtual activities (Zwift and the like) are left out unless asked for.
    include_virtual: bool = False
    # Heatmap only. Omitted: the athlete's home address and the smallest radius that holds half
    # of the activities.
    center_lat: float | None = Field(default=None, ge=-90, le=90)
    center_lon: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(default=None, gt=0, le=20_000)


class PosterOut(BaseModel):
    id: str
    kind: str
    year: int
    sports: list[str]
    include_virtual: bool
    center_lat: float | None
    center_lon: float | None
    radius_km: float | None
    status: str  # queued | running | done | failed
    error: str | None
    filename: str | None
    size_bytes: int | None
    activity_count: int | None
    created_at: datetime
    finished_at: datetime | None


class PosterSportOut(BaseModel):
    sport: str
    activities: int
    with_route: int  # how many have a GPS trace (the grid and the heatmap need one)


class HeatmapDefaultsOut(BaseModel):
    available: bool  # false when no selected activity has a start point
    center_lat: float | None = None
    center_lon: float | None = None
    radius_km: float | None = None
    # Where the centre came from: "home_address" (Settings -> Commute), "profile_home"
    # (Settings -> Profile) or "activities" (the middle of where they start).
    source: str | None = None
    activities: int = 0  # with a GPS trace
