"""Canonical, source-agnostic health data shapes — the health-domain counterpart to
`fit/types.py`. Produced by both `health/fit_parser.py` (device monitoring FIT files) and
`health/json_parser.py` (Garmin Connect-shaped daily summary/hydration JSON), consumed by
`health/ingest.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class HealthObservation:
    metric_key: str
    observed_at_utc: datetime
    local_date: str  # ISO date, e.g. "2025-01-01"
    aggregation: str  # "instant" | "interval" | "daily"
    value_num: float | None
    value_text: str | None = None
    unit: str | None = None
    interval_start: datetime | None = None
    interval_end: datetime | None = None


@dataclass(frozen=True)
class HealthStreamPoint:
    metric_key: str
    timestamp_utc: datetime
    value: float


@dataclass(frozen=True)
class ParsedSleepStage:
    stage: str
    start_time_utc: datetime
    end_time_utc: datetime


@dataclass(frozen=True)
class ParsedSleepSession:
    local_date: str
    start_time_utc: datetime
    end_time_utc: datetime
    total_sleep_s: float | None
    sleep_score: float | None
    stages: list[ParsedSleepStage] = field(default_factory=list)


@dataclass(frozen=True)
class HealthBatch:
    observations: list[HealthObservation] = field(default_factory=list)
    stream_points: list[HealthStreamPoint] = field(default_factory=list)
    sleep_sessions: list[ParsedSleepSession] = field(default_factory=list)
    # Field keys seen but not materialized as values — same "catalog, don't guess" contract
    # as CanonicalActivity.unrecognized_field_keys in fit/types.py.
    unrecognized_field_keys: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.observations
            or self.stream_points
            or self.sleep_sessions
            or self.unrecognized_field_keys
        )
