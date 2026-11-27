"""Request/response shapes for /settings/sync-schedule (when each source syncs by itself)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SyncSource = Literal["garmin_connect", "eufy", "kaya"]


class SyncSourceIn(BaseModel):
    source: SyncSource
    # "HH:MM" on the athlete's own clock; null = the deployment's default time.
    time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    enabled: bool = True
    # Garmin Connect only: also look for new activities every N hours (1 to 24) between the daily
    # runs; null = off. Left out of the request = unchanged.
    activity_check_hours: int | None = Field(default=None, ge=1, le=24)


class SyncScheduleIn(BaseModel):
    # Only the sources listed are changed.
    sources: list[SyncSourceIn] = Field(max_length=3)


class SyncSourceOut(BaseModel):
    source: SyncSource
    label: str
    # Connected (Garmin session, Eufy login, Kaya session); an unconnected source never runs.
    configured: bool
    enabled: bool
    time: str  # "HH:MM" on `timezone`'s clock
    is_default: bool  # no chosen time: the deployment's default
    timezone: str  # the IANA zone `time` is on
    next_run_at: datetime | None  # UTC instant; null when switched off or not connected
    # Garmin Connect only (null for the others): the hourly "new activities" check, null = off.
    activity_check_hours: int | None = None
    next_activity_check_at: datetime | None = None  # UTC instant; null when off


class SyncScheduleOut(BaseModel):
    sources: list[SyncSourceOut]
    # A source starts within this many minutes after its time.
    window_minutes: int
