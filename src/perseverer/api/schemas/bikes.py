"""API shapes for bikes and their tires."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class BikeIn(BaseModel):
    brand: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    year: int | None = Field(default=None, ge=1900, le=2100)
    size: str | None = Field(default=None, max_length=80)
    comments: str | None = Field(default=None, max_length=2_000)
    initial_distance_km: float = Field(default=0.0, ge=0, le=1_000_000)


class TireIn(BaseModel):
    brand: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    size: str | None = Field(default=None, max_length=80)
    comments: str | None = Field(default=None, max_length=2_000)
    initial_distance_km: float = Field(default=0.0, ge=0, le=1_000_000)
    max_distance_km: float | None = Field(default=None, gt=0, le=1_000_000)


class TireCreateIn(TireIn):
    """A new tire, optionally fitted to a bike straight away (front or rear)."""

    bike_id: str | None = None
    position: str | None = Field(default=None, pattern="^(front|rear)$")
    # The day it was fitted; defaults to now. Rides from that day on count toward the tire.
    installed_on: date | None = None


class TireInstallIn(BaseModel):
    bike_id: str
    position: str = Field(pattern="^(front|rear)$")
    installed_on: date | None = None


class TireOut(TireIn):
    id: str
    distance_km: float
    remaining_km: float | None
    over_limit: bool
    retired: bool
    # Where it is now: null bike = in stock (not on any bike).
    bike_id: str | None
    bike_name: str | None
    position: str | None
    installed_at: datetime | None
    created_at: datetime


class BikeOut(BikeIn):
    id: str
    distance_km: float
    retired: bool
    default_categories: list[str]
    front_tire: TireOut | None
    rear_tire: TireOut | None
    created_at: datetime


class DefaultBikeIn(BaseModel):
    bike_id: str


class ActivityBikeIn(BaseModel):
    bike_id: str | None = None


class ActivityBikeOut(BaseModel):
    bike_id: str | None
    # True when `bike_id` came from the dated category default rather than an explicit choice.
    is_default: bool
