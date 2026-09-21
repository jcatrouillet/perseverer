"""API shapes for athlete-owned gear.  Shoes are the first gear type."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ShoeIn(BaseModel):
    brand: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    size: str | None = Field(default=None, max_length=80)
    comments: str | None = Field(default=None, max_length=2_000)
    initial_distance_km: float = Field(default=0.0, ge=0, le=100_000)
    max_distance_km: float | None = Field(default=800.0, gt=0, le=100_000)


class ShoeOut(ShoeIn):
    id: str
    distance_km: float
    remaining_km: float | None
    over_limit: bool
    retired: bool
    default_sports: list[str]
    created_at: datetime


class DefaultShoeIn(BaseModel):
    shoe_id: str


class ActivityShoeIn(BaseModel):
    shoe_id: str | None = None


class ActivityShoeOut(BaseModel):
    shoe_id: str | None


class GearAlertOut(BaseModel):
    shoe_id: str
    brand: str
    model: str
    distance_km: float
    max_distance_km: float
