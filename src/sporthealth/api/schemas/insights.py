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
