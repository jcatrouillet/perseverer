"""Pure FIT parsing: `parse_fit(raw_bytes) -> CanonicalBatch`.

No I/O, no database, no network — a pure function over bytes, per CLAUDE.md's "raw first"
rule. Uses the official `garmin_fit_sdk` for correct profile/message decoding.

## Handling unknown data (never drop a field)

Real Garmin FIT files carry message types and fields the SDK's bundled profile doesn't name
— confirmed on real files used to build this parser (message types come back as digit-string
keys like "288" instead of e.g. "session_mesgs", and unnamed fields come back as int keys
mixed into an otherwise-named message's dict). This parser's rule for those:

- Every distinct field key seen, named or not, known message or not, gets registered into the
  metric_definition catalog (via the caller — see adapters/fit_folder.py) — so nothing is
  invisible, even if we don't yet know what it means.
- Fields on the summary-level messages we explicitly model (file_id, activity, session, lap,
  split) that aren't part of the small "core columns" mapping become `ParsedMetric` rows —
  bounded in number (one summary row per message), so this is cheap.
- High-frequency data — `record_mesgs` (confirmed, real per-second stream) and any entirely
  unrecognized message type with more than a handful of rows — is deliberately NOT expanded
  into per-occurrence value rows in Phase 1: for `record_mesgs` the well-understood channels
  go to the Parquet stream; for truly unrecognized high-frequency message types, only the
  catalog registration happens. The original bytes are always fully recoverable from
  raw_object regardless, so nothing is destroyed — see
  docs/adr/0002-phase-1-schema-and-ingestion.md for the full reasoning.
"""

from __future__ import annotations

import io
import math
from datetime import UTC, datetime
from typing import Any

from garmin_fit_sdk import Decoder, Stream

from perseverer.fit.types import (
    CanonicalActivity,
    CanonicalBatch,
    ParsedDevice,
    ParsedLap,
    ParsedMetric,
    ParsedSplit,
    ParsedWorkout,
    ParsedWorkoutStep,
    StreamPoint,
)

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
SEMICIRCLE_TO_DEGREES = 180.0 / (2.0**31)

# Rows beyond this count for a message type mean "this is stream-like data, not a summary" —
# see module docstring. record_mesgs is always treated as stream-like regardless of count.
_SUMMARY_ROW_LIMIT = 10

# Fields on session_mesgs mapped directly to CanonicalActivity attributes; anything else on
# session_mesgs becomes a generic ParsedMetric.
_SESSION_CORE_FIELDS = frozenset(
    {
        "timestamp",
        "start_time",
        "total_elapsed_time",
        "total_timer_time",
        "total_distance",
        "total_calories",
        "total_ascent",
        "sport",
        "sub_sport",
        "sport_profile_name",
        # Position fields: handled by _derive_route_endpoints (converted from semicircles),
        # not dumped as raw unitless integers via the generic metric fallback.
        "start_position_lat",
        "start_position_long",
        "end_position_lat",
        "end_position_long",
        "nec_lat",
        "nec_long",
        "swc_lat",
        "swc_long",
    }
)

# raw FIT record field name -> canonical stream channel name. "enhanced_*" variants are
# preferred over their legacy counterparts when both are present (see _record_channels).
_RECORD_CHANNEL_ALIASES: dict[str, str] = {
    "heart_rate": "heart_rate",
    "power": "power",
    "temperature": "temperature",
    "enhanced_speed": "speed_mps",
    "speed": "speed_mps",
    "enhanced_altitude": "altitude_m",
    "altitude": "altitude_m",
    "enhanced_respiration_rate": "respiration_rate",
    "respiration_rate": "respiration_rate",
    "distance": "distance_m",
}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        f = float(value)
        return None if math.isnan(f) else f
    return None


# Bouldering's per-route data, reverse-engineered rather than looked up -- confirmed absent from
# the installed garmin_fit_sdk's own profile for the "split" message (num 312; checked directly
# against `garmin_fit_sdk.profile.Profile["messages"][312]["fields"]`, which has no entry for any
# of these field numbers at all), and undocumented anywhere else. Cracked by decoding two real
# bouldering FIT files against their own athlete-recorded route logs (19 routes in one, 8 in the
# other) and pairing each "climb_active" split, in order, against the logged grade+status for
# that route -- both fields matched on all 27 routes with zero exceptions. A second, unsourced
# field list found afterward (elsewhere on the internet) claimed several more field numbers for
# this same message; cross-checked directly against both real files' own raw field keys before
# trusting any of it -- most of that list's claimed fields (0, 9, 13, 26, 27, 28, 32, 33, 34, 72,
# 73) never appear in either file at all (likely describes a different message, e.g. Garmin's
# `climb_pro`, used for outdoor/via-ferrata climbing, not indoor bouldering's own split_mesgs
# encoding), so only the fields below reflect fields this project actually verified are real:
#
# - `70`: V-scale grade, offset by +1 (V0=1, V1=2, ... -- de-offset below). The unsourced list's
#   broader "0-29, up to Font 9b" claim is consistent with what's verified here (only V0-V4 ever
#   appears in the two files this was checked against) but unconfirmed beyond that range.
# - `71`: result, confirmed only ever 2 ("Attempt") or 3 ("Completed") -- the unsourced list's
#   claim for this field matched exactly, independent corroboration of this project's own find.
# - `15`/`16`: avg/max heart rate for the split -- not from the original crack, but confirmed
#   against real data before trusting it: field 15 <= field 16 held on all 55 real splits across
#   both files (never once violated, exactly what avg <= max HR requires), and both fall in a
#   plausible bpm range against the athlete's own recorded resting HR in the same file. Present
#   on both "climb_active" and "climb_rest" splits (unlike grade/result, which only mean
#   something for the climb itself) -- gated below to climbing splits specifically since these
#   field numbers aren't confirmed to mean the same thing on a non-climbing split_type.
# - The unsourced list's claim for field `11` ("Temperature???", its own uncertainty markers)
#   does NOT hold up: it's a hard-constant 31 across both entire files, on two different days --
#   a real sensor reading wouldn't stay bit-for-bit identical across separate sessions. Left
#   unmapped rather than stored under a label this project couldn't actually verify.
_CLIMB_RESULT_BY_RAW_VALUE = {2: "attempt", 3: "completed"}
_CLIMB_SPLIT_TYPES = frozenset({"climb_active", "climb_rest"})


def _climb_fields(
    row: dict[Any, Any],
) -> tuple[int | None, str | None, float | None, float | None]:
    """Returns (grade, result, avg_hr, max_hr) -- grade/result are None on anything but a
    "climb_active" split; avg_hr/max_hr are None on anything but a climbing split at all."""
    split_type = row.get("split_type")
    if split_type not in _CLIMB_SPLIT_TYPES:
        return None, None, None, None

    avg_hr = _to_float(row.get(15))
    max_hr = _to_float(row.get(16))
    if split_type != "climb_active":
        return None, None, avg_hr, max_hr

    raw_grade = row.get(70)
    raw_result = row.get(71)
    grade = raw_grade - 1 if isinstance(raw_grade, int) else None
    if isinstance(raw_result, int):
        # An unrecognized raw value is stored, not dropped (CLAUDE.md's "never drop an unknown
        # field") -- only 2 and 3 have ever been confirmed, so a third value is real signal that
        # this reverse-engineered mapping is incomplete, not something to silently discard.
        result = _CLIMB_RESULT_BY_RAW_VALUE.get(raw_result, f"unknown_{raw_result}")
    else:
        result = None
    return grade, result, avg_hr, max_hr


def _to_int(value: Any) -> int | None:
    f = _to_float(value)
    return None if f is None else int(f)


def _message_short_name(message_type: str) -> str:
    if message_type.endswith("_mesgs"):
        return message_type[: -len("_mesgs")]
    return f"msg{message_type}"


def _metric_key(message_type: str, field_key: str | int) -> str:
    return f"fit.{_message_short_name(message_type)}.{field_key}"


def _semicircles_to_degrees(value: Any) -> float | None:
    f = _to_float(value)
    if f is None:
        return None
    return f * SEMICIRCLE_TO_DEGREES


def _generic_metrics_from_row(
    message_type: str, row: dict[Any, Any], exclude: frozenset[str]
) -> list[ParsedMetric]:
    metrics = []
    for field_key, value in row.items():
        if isinstance(field_key, str) and field_key in exclude:
            continue
        if isinstance(value, list):
            # Some fields (e.g. component fields) come back as lists; skip rather than guess.
            continue
        num = _to_float(value)
        text = None if num is not None else (str(value) if value is not None else None)
        if num is None and text is None:
            continue
        metrics.append(
            ParsedMetric(key=_metric_key(message_type, field_key), value_num=num, value_text=text)
        )
    return metrics


def _time_in_zone_metrics(row: dict[Any, Any]) -> list[ParsedMetric]:
    """Expands one `time_in_zone_mesgs` row into a `ParsedMetric` per zone index.

    `_generic_metrics_from_row` skips list-valued fields entirely (real component fields it
    can't interpret) -- `time_in_zone_mesgs` is the one message type where that would throw
    away the *entire point* of the message: `time_in_hr_zone` (seconds per HR zone) and its
    `hr_zone_high_boundary` companion (each zone's upper bpm bound) are both arrays, confirmed
    against real device data (introspected directly, not assumed from the FIT SDK's profile
    docs) -- one real file carried `time_in_hr_zone: [17.573, 12.0, 80.003, 2096.94, 0, 0, 0]`
    alongside `hr_zone_high_boundary: [89, 105, 124, 140, 159, 175]`: 7 time buckets for 6
    configured zone boundaries, where index 0 is "time below zone 1". Power/speed/cadence zones
    (`time_in_power_zone` etc.) follow the identical shape when a device reports them (e.g. a
    power meter) and are expanded the same way, generically -- not hardcoded to HR only.

    Zone *count* is read from whatever the device actually reported, never assumed to be 5.
    """
    metrics: list[ParsedMetric] = []
    for field_key, value in row.items():
        if isinstance(value, list):
            for i, item in enumerate(value):
                num = _to_float(item)
                if num is None:
                    continue
                metrics.append(
                    ParsedMetric(
                        key=f"{_metric_key('time_in_zone_mesgs', field_key)}_{i}",
                        value_num=num,
                    )
                )
        else:
            num = _to_float(value)
            text = None if num is not None else (str(value) if value is not None else None)
            if num is None and text is None:
                continue
            metrics.append(
                ParsedMetric(
                    key=_metric_key("time_in_zone_mesgs", field_key), value_num=num, value_text=text
                )
            )
    return metrics


_WORKOUT_STEP_CORE_FIELDS = frozenset(
    {
        "message_index",
        "duration_type",
        "duration_time",
        "duration_distance",
        "duration_value",
        "target_type",
        "target_value",
        "secondary_target_value",
        "custom_target_speed_low",
        "custom_target_speed_high",
        "custom_target_value_low",
        "custom_target_value_high",
        "target_speed_zone",
        "intensity",
        "duration_step",
        "repeat_steps",
    }
)


def _parse_workout_step(row: dict[Any, Any]) -> ParsedWorkoutStep:
    duration_type = row.get("duration_type")
    target_type = row.get("target_type")
    # `custom_target_speed_low/high` is the SDK's own descaled m/s field, populated alongside
    # the raw `custom_target_value_low/high` scaled int whenever target_type == "speed" --
    # confirmed against a real structured-workout FIT file (5x1km threshold session). Other
    # target types (heart_rate, cadence, power, open) aren't extracted into a range here since
    # nothing consumes them yet -- the raw row is still fully cataloged via the generic-metrics
    # fallback below, so no field is lost, just not modeled as a pace range.
    target_low = _to_float(row.get("custom_target_speed_low")) if target_type == "speed" else None
    target_high = _to_float(row.get("custom_target_speed_high")) if target_type == "speed" else None
    return ParsedWorkoutStep(
        step_index=_to_int(row.get("message_index")) or 0,
        duration_type=duration_type,
        duration_time_s=_to_float(row.get("duration_time")),
        duration_distance_m=_to_float(row.get("duration_distance")),
        target_type=target_type,
        target_low_mps=target_low,
        target_high_mps=target_high,
        intensity=row.get("intensity"),
        # Only meaningful when duration_type == "repeat_until_steps_cmplt": "repeat steps
        # [duration_step..this step's index - 1] repeat_steps times" (FIT's own semantics,
        # confirmed against real data -- a step with duration_step=1, repeat_steps=5 at
        # message_index 3 means "repeat steps 1-2, five times"). `.get()`, not bracket access --
        # a malformed/partial repeat step must not crash the whole parse.
        repeat_from_step=(
            _to_int(row.get("duration_step"))
            if duration_type == "repeat_until_steps_cmplt"
            else None
        ),
        repeat_count=(
            _to_int(row.get("repeat_steps"))
            if duration_type == "repeat_until_steps_cmplt"
            else None
        ),
    )


def _parse_workout(
    workout_row: dict[Any, Any] | None, step_rows: list[dict[Any, Any]]
) -> tuple[ParsedWorkout | None, list[ParsedMetric]]:
    """`workout_mesgs`/`workout_step_mesgs` carry a pre-planned workout structure downloaded to
    the device before the activity (Garmin Connect's own "Workout" builder) -- confirmed real:
    a warmup, a 5x-repeated [1km interval, 75s recovery] block, and a cooldown, each with a
    target pace range, on a genuine structured-workout FIT file. Both message types used to fall
    through `parse_fit`'s generic "unrecognized message type" handling, which only catalogs
    `rows[0]` for a low-row-count type -- silently dropping every step but the first. Modeled
    explicitly here instead; any field on a step row not in `_WORKOUT_STEP_CORE_FIELDS` still
    becomes a generic metric (per-step, keyed by step index) so nothing is lost either way."""
    if workout_row is None and not step_rows:
        return None, []

    # `wkt_name` decodes as a list of string fragments on real files (only the first is the
    # actual name; the rest are empty/garbage) -- `_generic_metrics_from_row` skips list-valued
    # fields entirely, so this needs its own handling rather than falling through to it.
    name = None
    if workout_row is not None:
        wkt_name = workout_row.get("wkt_name")
        if isinstance(wkt_name, list) and wkt_name and isinstance(wkt_name[0], str):
            name = wkt_name[0] or None
        elif isinstance(wkt_name, str):
            name = wkt_name or None

    extra_metrics: list[ParsedMetric] = []
    if workout_row is not None:
        extra_metrics += _generic_metrics_from_row(
            "workout_mesgs", workout_row, frozenset({"wkt_name"})
        )

    steps = []
    for row in sorted(step_rows, key=lambda r: r.get("message_index") or 0):
        steps.append(_parse_workout_step(row))
        step_index = int(row.get("message_index") or 0)
        extra_metrics += _generic_metrics_from_row(
            f"workout_step_{step_index}_mesgs", row, _WORKOUT_STEP_CORE_FIELDS
        )

    description = workout_row.get("wkt_description") if workout_row else None
    if isinstance(description, list):
        description = description[0] if description and isinstance(description[0], str) else None

    workout = ParsedWorkout(name=name, description=description or None, steps=steps)
    return workout, extra_metrics


def _parse_device(file_id_row: dict[Any, Any] | None) -> ParsedDevice | None:
    if not file_id_row:
        return None
    serial = file_id_row.get("serial_number")
    return ParsedDevice(
        manufacturer=file_id_row.get("manufacturer"),
        product=file_id_row.get("garmin_product") or (
            str(file_id_row["product"]) if file_id_row.get("product") is not None else None
        ),
        serial_number=str(serial) if serial is not None else None,
    )


_MAX_PLAUSIBLE_UTC_OFFSET_S = 16 * 3600  # real-world zones span -12h..+14h; a little slack either
# side for old/fringe zones, but nowhere near enough to hide a units/epoch bug in the field.


def _derive_utc_offset_s(activity_row: dict[Any, Any] | None) -> int:
    """A real device quirk (confirmed against 14 real indoor-cycling activities, all recorded
    with no GPS fix): `local_timestamp` occasionally comes back corrupt -- off by roughly a
    billion seconds, wildly outside any real timezone -- rather than absent, so it can't be
    caught by the isinstance/None checks above it. Rather than let that silently corrupt
    `local_date` by decades (as it did before this bound existed), an implausible result falls
    back to 0, the same "nothing better available" fallback already used when the fields are
    missing entirely."""
    if not activity_row:
        return 0
    ts = activity_row.get("timestamp")
    local_ts = activity_row.get("local_timestamp")
    if not isinstance(ts, datetime) or not isinstance(local_ts, int | float):
        return 0
    utc_seconds = (ts - FIT_EPOCH).total_seconds()
    offset_s = round(float(local_ts) - utc_seconds)
    if abs(offset_s) > _MAX_PLAUSIBLE_UTC_OFFSET_S:
        return 0
    return offset_s


_KNOWN_RECORD_FIELDS = frozenset(
    {
        "timestamp",
        "heart_rate",
        "power",
        "temperature",
        "speed",
        "enhanced_speed",
        "altitude",
        "enhanced_altitude",
        "respiration_rate",
        "enhanced_respiration_rate",
        "distance",
        "cadence",
        "fractional_cadence",
        "position_lat",
        "position_long",
    }
)


def _record_channels(row: dict[Any, Any], unrecognized: set[str]) -> dict[str, float]:
    for key in row:
        if not (isinstance(key, str) and key in _KNOWN_RECORD_FIELDS):
            unrecognized.add(_metric_key("record_mesgs", key))

    values: dict[str, float] = {}
    # Prefer enhanced_* over the legacy field when both are present.
    for raw_name in ("power", "temperature", "heart_rate"):
        num = _to_float(row.get(raw_name))
        if num is not None:
            values[_RECORD_CHANNEL_ALIASES[raw_name]] = num
    for legacy, enhanced, canonical in (
        ("speed", "enhanced_speed", "speed_mps"),
        ("altitude", "enhanced_altitude", "altitude_m"),
        ("respiration_rate", "enhanced_respiration_rate", "respiration_rate"),
    ):
        num = _to_float(row.get(enhanced))
        if num is None:
            num = _to_float(row.get(legacy))
        if num is not None:
            values[canonical] = num
    distance = _to_float(row.get("distance"))
    if distance is not None:
        values["distance_m"] = distance
    cadence = _to_float(row.get("cadence"))
    if cadence is not None:
        fractional = _to_float(row.get("fractional_cadence")) or 0.0
        values["cadence"] = cadence + fractional
    lat = _semicircles_to_degrees(row.get("position_lat"))
    lon = _semicircles_to_degrees(row.get("position_long"))
    if lat is not None and lon is not None:
        values["lat"] = lat
        values["lon"] = lon
    return values


_LatLon = tuple[float, float]
_BBox = tuple[float, float, float, float]


def _derive_route_endpoints(
    session: dict[Any, Any], route_points: list[_LatLon]
) -> tuple[_LatLon | None, _LatLon | None, _BBox | None]:
    """Prefer record-level GPS; fall back to session-level start/end/bbox fields (present
    even for e.g. indoor/treadmill activities that carry no per-record position)."""
    if route_points:
        lats = [p[0] for p in route_points]
        lons = [p[1] for p in route_points]
        return route_points[0], route_points[-1], (min(lats), min(lons), max(lats), max(lons))

    start = None
    start_lat = _semicircles_to_degrees(session.get("start_position_lat"))
    start_lon = _semicircles_to_degrees(session.get("start_position_long"))
    if start_lat is not None and start_lon is not None:
        start = (start_lat, start_lon)

    end = None
    end_lat = _semicircles_to_degrees(session.get("end_position_lat"))
    end_lon = _semicircles_to_degrees(session.get("end_position_long"))
    if end_lat is not None and end_lon is not None:
        end = (end_lat, end_lon)

    bbox = None
    nec_lat = _semicircles_to_degrees(session.get("nec_lat"))
    nec_lon = _semicircles_to_degrees(session.get("nec_long"))
    swc_lat = _semicircles_to_degrees(session.get("swc_lat"))
    swc_lon = _semicircles_to_degrees(session.get("swc_long"))
    if None not in (nec_lat, nec_lon, swc_lat, swc_lon):
        assert nec_lat is not None
        assert nec_lon is not None
        assert swc_lat is not None
        assert swc_lon is not None
        bbox = (
            min(nec_lat, swc_lat),
            min(nec_lon, swc_lon),
            max(nec_lat, swc_lat),
            max(nec_lon, swc_lon),
        )
    elif start and end:
        bbox = (
            min(start[0], end[0]),
            min(start[1], end[1]),
            max(start[0], end[0]),
            max(start[1], end[1]),
        )

    return start, end, bbox


def parse_fit(raw_bytes: bytes) -> CanonicalBatch:
    stream = Stream.from_bytes_io(io.BytesIO(raw_bytes))
    decoder = Decoder(stream)
    messages, errors = decoder.read()
    if errors:
        raise ValueError(f"FIT decode errors: {errors}")

    if "session_mesgs" not in messages or not messages["session_mesgs"]:
        # Not an activity file (e.g. monitoring/sleep) — parses cleanly, just not modeled yet.
        return CanonicalBatch(
            kind="unrecognized",
            activity=None,
            unrecognized_message_types=sorted(messages.keys()),
        )

    session = messages["session_mesgs"][0]
    activity_row = messages.get("activity_mesgs", [None])[0]
    file_id_row = messages.get("file_id_mesgs", [None])[0]

    start_time = session.get("start_time")
    if not isinstance(start_time, datetime):
        raise ValueError("session_mesgs missing a valid start_time")

    extra_metrics = _generic_metrics_from_row("session_mesgs", session, _SESSION_CORE_FIELDS)
    if activity_row:
        extra_metrics += _generic_metrics_from_row(
            "activity_mesgs", activity_row, frozenset({"timestamp", "local_timestamp"})
        )
    if file_id_row:
        extra_metrics += _generic_metrics_from_row(
            "file_id_mesgs",
            file_id_row,
            frozenset({"manufacturer", "product", "garmin_product", "serial_number", "type"}),
        )

    # `time_in_zone_mesgs` carries one row per lap PLUS one whole-activity row, distinguished
    # by `reference_mesg` -- only the whole-activity ("session") row is what an activity-level
    # "time in zones" view wants; per-lap zone breakdowns aren't modeled here (no lap-level UI
    # consumes them yet, and lap.py's ParsedLap has no field for it). Confirmed some real
    # devices/firmware never emit this message at all -- absent is absent, not an error.
    session_zone_row = next(
        (
            row
            for row in messages.get("time_in_zone_mesgs", [])
            if row.get("reference_mesg") == "session"
        ),
        None,
    )
    if session_zone_row is not None:
        extra_metrics += _time_in_zone_metrics(session_zone_row)

    workout, workout_metrics = _parse_workout(
        messages.get("workout_mesgs", [None])[0] if messages.get("workout_mesgs") else None,
        messages.get("workout_step_mesgs", []),
    )
    extra_metrics += workout_metrics

    laps = []
    for i, row in enumerate(messages.get("lap_mesgs", [])):
        lap_start = row.get("start_time")
        if not isinstance(lap_start, datetime):
            continue
        laps.append(
            ParsedLap(
                lap_index=i,
                start_time_utc=lap_start,
                duration_s=_to_float(row.get("total_elapsed_time")),
                moving_duration_s=_to_float(row.get("total_timer_time")),
                distance_m=_to_float(row.get("total_distance")),
                avg_hr=_to_float(row.get("avg_heart_rate")),
                max_hr=_to_float(row.get("max_heart_rate")),
                avg_speed_mps=_to_float(row.get("enhanced_avg_speed"))
                or _to_float(row.get("avg_speed")),
            )
        )

    splits = []
    for i, row in enumerate(messages.get("split_mesgs", [])):
        climb_grade, climb_result, climb_avg_hr, climb_max_hr = _climb_fields(row)
        splits.append(
            ParsedSplit(
                split_index=i,
                split_type=row.get("split_type"),
                start_time_utc=row.get("start_time"),
                end_time_utc=row.get("end_time"),
                duration_s=_to_float(row.get("total_elapsed_time")),
                distance_m=_to_float(row.get("total_distance")),
                climb_grade=climb_grade,
                climb_result=climb_result,
                climb_avg_hr=climb_avg_hr,
                climb_max_hr=climb_max_hr,
            )
        )

    unrecognized_field_keys: set[str] = set()

    stream_points: list[StreamPoint] = []
    route_points: list[tuple[float, float]] = []
    for row in messages.get("record_mesgs", []):
        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        values = _record_channels(row, unrecognized_field_keys)
        if values:
            stream_points.append(StreamPoint(timestamp_utc=ts, values=values))
        if "lat" in values and "lon" in values:
            route_points.append((values["lat"], values["lon"]))

    # Message types we've explicitly modeled above; everything else is either a low-frequency
    # summary-like message (cataloged as extra metrics) or high-frequency unrecognized data
    # (cataloged for visibility only — see module docstring).
    handled = {
        "file_id_mesgs",
        "session_mesgs",
        "activity_mesgs",
        "lap_mesgs",
        "split_mesgs",
        "record_mesgs",
        "time_in_zone_mesgs",
        # workout_step_mesgs is inherently multi-row (one row per step) even for a short
        # workout -- the generic "rows[0] only" fallback below would silently drop every step
        # but the first, which is exactly the bug _parse_workout above exists to avoid.
        "workout_mesgs",
        "workout_step_mesgs",
    }
    unrecognized_types = []
    for message_type, rows in messages.items():
        if message_type in handled or not rows:
            continue
        if len(rows) <= _SUMMARY_ROW_LIMIT:
            extra_metrics += _generic_metrics_from_row(message_type, rows[0], frozenset())
        else:
            unrecognized_types.append(message_type)
            for key in rows[0]:
                unrecognized_field_keys.add(_metric_key(message_type, key))

    device = _parse_device(file_id_row)
    route_start, route_end, route_bbox = _derive_route_endpoints(session, route_points)
    altitudes = [p.values["altitude_m"] for p in stream_points if "altitude_m" in p.values]
    max_altitude_m = max(altitudes) if altitudes else None

    activity = CanonicalActivity(
        start_time_utc=start_time,
        utc_offset_s=_derive_utc_offset_s(activity_row),
        sport=session.get("sport") or "unknown",
        sub_sport=session.get("sub_sport"),
        name=session.get("sport_profile_name"),
        duration_s=_to_float(session.get("total_elapsed_time")),
        moving_duration_s=_to_float(session.get("total_timer_time")),
        distance_m=_to_float(session.get("total_distance")),
        elevation_gain_m=_to_float(session.get("total_ascent")),
        max_altitude_m=max_altitude_m,
        calories=_to_float(session.get("total_calories")),
        device=device,
        laps=laps,
        splits=splits,
        workout=workout,
        stream=stream_points,
        route_points=route_points,
        route_start=route_start,
        route_end=route_end,
        route_bbox=route_bbox,
        extra_metrics=extra_metrics,
        unrecognized_field_keys=sorted(unrecognized_field_keys),
    )

    return CanonicalBatch(
        kind="activity", activity=activity, unrecognized_message_types=unrecognized_types
    )
