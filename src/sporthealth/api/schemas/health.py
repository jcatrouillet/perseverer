"""Response model for GET /health/observations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthObservationOut(BaseModel):
    metric_key: str
    observed_at_utc: datetime
    local_date: str
    aggregation: str
    value_num: float | None
    value_text: str | None
    unit: str | None
    source: str
