"""Canonical, source-agnostic data shapes produced by `fit.parser.parse_fit`.

These are the pure-Python contract between "parsed a FIT file" and "wrote it into SQLite/
Parquet" — the ingest step (fit_folder.ingest) consumes them, and nothing about them is
FIT-specific in shape, so a future adapter (e.g. a Garmin Connect JSON parser) can target the
same types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ParsedMetric:
    """One open-ended fact: an activity-level field with no dedicated column."""

    key: str
    value_num: float | None
    value_text: str | None = None
    unit: str | None = None


@dataclass(frozen=True)
class ParsedLap:
    lap_index: int
    start_time_utc: datetime
    duration_s: float | None
    # FIT's own `total_timer_time` -- excludes any device pause within the lap, the same way
    # session-level `moving_duration_s` excludes it for the whole activity. `duration_s` above
    # is `total_elapsed_time`, real wall-clock and equal to this UNLESS the athlete paused mid-
    # lap, in which case `duration_s` includes the pause and this doesn't (confirmed against a
    # real recovery-interval lap: elapsed 1008.2s vs timer 90.0s across a genuine ~15min pause).
    moving_duration_s: float | None
    distance_m: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_speed_mps: float | None


@dataclass(frozen=True)
class ParsedSplit:
    split_index: int
    split_type: str | None
    start_time_utc: datetime | None
    end_time_utc: datetime | None
    duration_s: float | None
    distance_m: float | None


@dataclass(frozen=True)
class StreamPoint:
    timestamp_utc: datetime
    values: dict[str, float]


@dataclass(frozen=True)
class ParsedDevice:
    manufacturer: str | None
    product: str | None
    serial_number: str | None


@dataclass(frozen=True)
class ParsedWorkoutStep:
    """One step of a pre-planned workout structure (Garmin Connect's "Workout" builder,
    downloaded to the device before the activity and recorded verbatim into the FIT file's own
    `workout_step_mesgs` -- confirmed real via a genuine structured-workout FIT file: a warmup,
    a 5x-repeated [interval, recovery] block, and a cooldown, each carrying a target pace range).
    A `duration_type` of `"repeat_until_steps_cmplt"` is itself a step (not a target/intensity
    one) whose `repeat_from_step`/`repeat_count` describe "repeat steps
    [repeat_from_step..this step's index - 1] `repeat_count` times" -- callers that want the
    actual, flattened sequence of steps as executed (e.g. to align with recorded laps, which the
    device creates one-per-step) expand these themselves; this type stores the raw FIT
    structure, unexpanded, per "raw first"."""

    step_index: int  # FIT's own message_index -- the step's position in the workout.
    duration_type: str | None
    duration_time_s: float | None
    duration_distance_m: float | None
    target_type: str | None
    # Only populated for target_type == "speed" (this project's only real-data-confirmed case;
    # see module docstring) -- m/s, SI per CLAUDE.md. Other target types (heart_rate, cadence,
    # power, open) are recognized but their target range isn't extracted here, since nothing
    # consumes it yet; the raw workout_step_mesgs row is still fully archived regardless.
    target_low_mps: float | None
    target_high_mps: float | None
    intensity: str | None
    repeat_from_step: int | None
    repeat_count: int | None


@dataclass(frozen=True)
class ParsedWorkout:
    name: str | None
    description: str | None
    steps: list[ParsedWorkoutStep] = field(default_factory=list)


@dataclass(frozen=True)
class CanonicalActivity:
    start_time_utc: datetime
    utc_offset_s: int
    sport: str
    sub_sport: str | None
    name: str | None
    duration_s: float | None
    moving_duration_s: float | None
    distance_m: float | None
    elevation_gain_m: float | None
    calories: float | None
    device: ParsedDevice | None
    laps: list[ParsedLap] = field(default_factory=list)
    splits: list[ParsedSplit] = field(default_factory=list)
    workout: ParsedWorkout | None = None
    stream: list[StreamPoint] = field(default_factory=list)
    route_points: list[tuple[float, float]] = field(default_factory=list)  # (lat, lon) degrees
    # Falls back to session-level start/end position when record_mesgs carries no GPS (e.g.
    # an indoor/treadmill activity) — see _derive_route_endpoints in parser.py.
    route_start: tuple[float, float] | None = None
    route_end: tuple[float, float] | None = None
    # (min_lat, min_lng, max_lat, max_lng)
    route_bbox: tuple[float, float, float, float] | None = None
    extra_metrics: list[ParsedMetric] = field(default_factory=list)
    # Field keys seen but not materialized as values anywhere (unrecognized fields inside
    # record_mesgs, or fields of a high-frequency unrecognized message type) — cataloged in
    # metric_definition by the ingest layer so nothing is invisible, without storing a value
    # for data whose meaning isn't known yet. See fit/parser.py module docstring.
    unrecognized_field_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CanonicalBatch:
    """The output of parsing one raw object. `kind == "unrecognized"` means the file parsed
    without error but wasn't an activity FIT file (e.g. a monitoring/sleep file) — see
    docs/adr/0002-phase-1-schema-and-ingestion.md for why full modeling of those is Phase 2.
    """

    kind: str  # "activity" | "unrecognized"
    activity: CanonicalActivity | None
    unrecognized_message_types: list[str] = field(default_factory=list)
