"""Shared pure-Python shapes for the insight engine (Phase 8, ADR 0012). `InsightActivity` is
the bounded, already-flattened view of one activity every rule module needs -- assembled by
`engine.py` (which has DB access) from `activity` plus the handful of `activity_metric` EAV
rows each rule actually reads (elevation loss, cadence, weather), so rule modules themselves
stay pure functions with no database access, exactly like `merge/engine.py`'s own precedent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class InsightActivity:
    id: str
    start_time_utc: datetime
    local_date: str
    sport: str
    sport_family: str
    name: str | None
    distance_m: float | None
    duration_s: float | None
    moving_duration_s: float | None
    avg_hr: float | None
    max_hr: float | None
    cadence: float | None
    max_cadence: float | None
    elevation_gain_m: float | None
    elevation_loss_m: float | None
    temperature_min_c: float | None
    temperature_max_c: float | None
    # Defaults to 0 (UTC) rather than being required -- only rules_efforts.py's start-time-of-day
    # dimensions need it (see its own _start_hour docstring for why: comparing raw UTC hours
    # across activities logged in different local timezones, or with a bogus midnight-UTC
    # placeholder timestamp, produces a nonsense "earliest start" winner).
    utc_offset_s: int = 0
    # Bouldering route metrics are flattened from the activity's climb_active split rows by
    # insights.engine. They stay optional because normal activities have no such rows.
    climb_route_count: int | None = None
    climb_max_attempted_grade: int | None = None
    climb_max_completed_grade: int | None = None
    climb_time_s: float | None = None


@dataclass(frozen=True)
class Insight:
    kind: str
    window: str
    subject_key: str
    title: str
    detail: dict[str, object] = field(default_factory=dict)
    value_num: float | None = None
    metric_key: str | None = None
    sport_family: str | None = None
    activity_id: str | None = None
    local_date: str | None = None
