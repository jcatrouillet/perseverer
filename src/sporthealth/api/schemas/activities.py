"""Response models for GET /activities and GET /activities/{id}."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DeviceOut(BaseModel):
    manufacturer: str | None
    product: str | None
    serial_number: str | None


class LapOut(BaseModel):
    lap_index: int
    start_time_utc: datetime
    duration_s: float | None
    distance_m: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_speed_mps: float | None


class SplitOut(BaseModel):
    split_index: int
    split_type: str | None
    start_time_utc: datetime | None
    end_time_utc: datetime | None
    duration_s: float | None
    distance_m: float | None


class RouteOut(BaseModel):
    encoded_polyline: str | None
    min_lat: float | None
    min_lng: float | None
    max_lat: float | None
    max_lng: float | None
    start_lat: float | None
    start_lng: float | None
    end_lat: float | None
    end_lng: float | None


class ActivityMetricOut(BaseModel):
    metric_key: str
    value_num: float | None
    value_text: str | None
    unit: str | None
    source: str


class ActivitySummary(BaseModel):
    id: str
    start_time_utc: datetime
    utc_offset_s: int
    local_date: str | None
    sport: str
    sub_sport: str | None
    name: str | None
    duration_s: float | None
    moving_duration_s: float | None
    distance_m: float | None
    elevation_gain_m: float | None
    calories: float | None
    avg_hr_bpm: float | None
    max_hr_bpm: float | None
    training_load: float | None
    # Borg CR10 effort rating (0-10, half-point resolution): fit.session.workout_rpe's raw FIT
    # value is the same scale x10 (see routers/activities.py's read of this field for the
    # profile.py introspection that confirmed this) -- this is the descaled, presentation value.
    workout_rpe: float | None
    primary_source: str
    stream_available: bool


class ActivityDetail(ActivitySummary):
    device: DeviceOut | None
    laps: list[LapOut]
    splits: list[SplitOut]
    route: RouteOut | None
    metrics: list[ActivityMetricOut]


class ActivityContextRecentOut(BaseModel):
    id: str
    local_date: str | None
    distance_m: float
    # Moving-preferred effective duration (moving_duration_s, falling back to duration_s) --
    # same convention as the frontend's effectiveDurationS(), computed once here rather than
    # exposing both raw fields and making every consumer re-derive it.
    duration_s: float


class ActivityContextOut(BaseModel):
    # None when there are no other same-sport, similar-distance activities to compare against
    # (comparable_count == 0) -- never a fabricated number for a first-of-its-kind effort.
    percentile_rank: float | None
    comparable_count: int
    recent: list[ActivityContextRecentOut]
