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


class SyncScheduleOut(BaseModel):
    sources: list[SyncSourceOut]
    # A source starts within this many minutes after its time.
    window_minutes: int
