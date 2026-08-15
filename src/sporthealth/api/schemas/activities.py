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
    # The athlete's recorded body weight at the time of this specific activity
    # (fit.user_profile.weight, a generic per-session field -- present on ~99% of activities,
    # confirmed against the real archive). Exposed so the frontend can derive MET-minutes
    # (calories / weight_kg, a standard gross-MET approximation) without a second per-activity
    # fetch -- not itself a metric anyone reads directly.
    weight_kg: float | None
    primary_source: str
    stream_available: bool


class ActivityDetail(ActivitySummary):
    device: DeviceOut | None
    laps: list[LapOut]
    splits: list[SplitOut]
    route: RouteOut | None
    metrics: list[ActivityMetricOut]
    # Not a stored per-activity field -- Garmin logs this to the athlete's daily hydration log,
    # not to the activity itself, so it's matched by nearest timestamp at read time (see
    # routers/activities.py::_estimated_sweat_loss_ml for the real-data verification that
    # justified this). None whenever no hydration-log entry lands close enough after the
    # activity to be confidently this one's -- never a guessed number.
    estimated_sweat_loss_ml: float | None


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


class ActivityWeatherOut(BaseModel):
    # False whenever there's nothing to show -- no GPS start point to query against, or the
    # Open-Meteo fetch failed/returned no usable data for this activity's time window. Never a
    # fabricated range (CLAUDE.md's raw-first rule): every non-null field below came from a real
    # archived Open-Meteo response.
    available: bool
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    humidity_min_pct: float | None = None
    humidity_max_pct: float | None = None
    # WMO weather code (https://open-meteo.com/en/docs -- the same taxonomy the archive API
    # returns) at the hour closest to the activity's own start. Icon mapping is a frontend
    # presentation concern, not modeled here.
    weather_code: int | None = None


class ActivityMapPointOut(BaseModel):
    id: str
    local_date: str | None
    sport: str
    name: str | None
    distance_m: float | None
    start_lat: float
    start_lng: float


class ActivityRouteOut(BaseModel):
    id: str
    # simplified_polyline (not encoded_polyline): a batch response for a page of thumbnail-sized
    # maps should stay small, and simplified_polyline is exactly the "cheaper to render" variant
    # route_geom already carries (currently identical bytes to encoded_polyline -- see ADR 0002 --
    # but this is the field that would shrink if real Douglas-Peucker simplification lands later,
    # so callers that only need a thumbnail shouldn't have to change).
    simplified_polyline: str | None
