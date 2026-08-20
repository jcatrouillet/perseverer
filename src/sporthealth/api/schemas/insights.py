"""Response model for GET /insights."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class InsightOut(BaseModel):
    kind: str
    window: str
    title: str
    detail: dict[str, object]
    value_num: float | None
    metric_key: str | None
    sport_family: str | None
    activity_id: str | None
    local_date: str | None
    computed_at: datetime


class PaceBandOut(BaseModel):
    """One `pace_bands.PACE_BANDS` entry, total seconds across the athlete's whole running
    history. Always present for every defined band, even when `seconds` is 0 -- so a client can
    render every band's column without checking for missing entries."""

    label: str
    seconds: float
