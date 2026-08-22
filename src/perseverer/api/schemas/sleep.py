"""Response model for GET /sleep."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SleepStageOut(BaseModel):
    stage: str
    start_time_utc: datetime
    end_time_utc: datetime


class SleepSessionOut(BaseModel):
    local_date: str
    start_time_utc: datetime
    end_time_utc: datetime
    total_sleep_s: float | None
    sleep_score: float | None
    source: str
    stages: list[SleepStageOut]
