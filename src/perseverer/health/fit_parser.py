"""Pure health-domain FIT parsing: `parse_health_fit(raw_bytes) -> HealthBatch`.

Handles Garmin device monitoring FIT file kinds confirmed against real files (WELLNESS,
METRICS, HRV_STATUS, SLEEP_DATA, NAP — see docs/adr/0004-phase-2-health-ingestion.md) without
gating on filename: whichever known message types are present get extracted, so this is
robust to a real export archive organizing these files differently than this project's sample
set. Called after `fit.parser.parse_fit` returns `kind != "activity"` — see
`ingest_dispatch.py`.

## Scope boundary (see ADR 0004 for the full reasoning)

- `monitoring_mesgs` is mined for `heart_rate` only. Daily steps/distance/calories are NOT
  reconstructed from its compressed `cycles` fields (needs per-activity-type conversion
  factors from `monitoring_info_mesgs` — complex, and redundant: the daily-summary JSON already
  has clean, server-computed daily totals for exactly these fields).
- `monitoring_mesgs.timestamp_16` (FIT's compressed 16-bit rolling timestamp — confirmed
  necessary: 305/445 rows in one real file used it) is reconstructed via
  `_resolve_compressed_timestamps`; every other message type in these files carries a full,
  uncompressed `timestamp` in every row (verified against real files, not assumed).
"""

from __future__ import annotations

import io
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from garmin_fit_sdk import Decoder, Stream

from perseverer.health.types import (
    HealthBatch,
    HealthObservation,
    HealthStreamPoint,
    ParsedSleepSession,
    ParsedSleepStage,
)

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)

# Message types this parser explicitly models. Anything else (or an unnamed field within one
# of these) is cataloged via unrecognized_field_keys, never silently dropped.
_HANDLED_MESSAGE_TYPES = frozenset(
    {
        "file_id_mesgs",
        "file_creator_mesgs",
        "device_info_mesgs",
        "software_mesgs",
        "ohr_settings_mesgs",
        "event_mesgs",
        "timestamp_correlation_mesgs",
        "monitoring_info_mesgs",
        "monitoring_mesgs",
        "monitoring_hr_data_mesgs",
        "stress_level_mesgs",
        "respiration_rate_mesgs",
        "spo2_data_mesgs",
        "hrv_value_mesgs",
        "hrv_status_summary_mesgs",
        "sleep_level_mesgs",
        "sleep_assessment_mesgs",
        "nap_event_mesgs",
    }
)

# hrv_status_summary_mesgs fields mapped to named metrics; every other numeric field on that
# message (e.g. the baseline band) still becomes a generic fit.hrv_status_summary.<field>
# observation below — this list only controls which get a clean, documented metric_key.
_HRV_STATUS_NAMED_FIELDS: dict[str, tuple[str, str]] = {
    "weekly_average": ("hrv.weekly_average", "ms"),
    "last_night_average": ("hrv.last_night_average", "ms"),
    "last_night_5_min_high": ("hrv.last_night_5_min_high", "ms"),
}


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        f = float(value)
        return None if math.isnan(f) else f
    return None


def _message_short_name(message_type: str) -> str:
    if message_type.endswith("_mesgs"):
        return message_type[: -len("_mesgs")]
    return f"msg{message_type}"


def _metric_key(message_type: str, field_key: str | int) -> str:
    return f"fit.{_message_short_name(message_type)}.{field_key}"


def _resolve_compressed_timestamps(
    rows: list[dict[Any, Any]], seed_timestamp: datetime | None
) -> list[dict[Any, Any]]:
    """Reconstructs FIT's compressed 16-bit rolling timestamp against the most recently seen
    full timestamp, handling 16-bit rollover. Returns new dicts with `timestamp` always
    present (never `timestamp_16`), in the same order.
    """
    resolved: list[dict[Any, Any]] = []
    last_full: int | None = None
    if seed_timestamp is not None:
        last_full = int((seed_timestamp - FIT_EPOCH).total_seconds())

    for original_row in rows:
        row = dict(original_row)
        ts = row.get("timestamp")
        if isinstance(ts, datetime):
            last_full = int((ts - FIT_EPOCH).total_seconds())
        elif "timestamp_16" in row and last_full is not None:
            t16 = row.pop("timestamp_16")
            base = last_full & ~0xFFFF
            candidate = base + t16
            if candidate < last_full:
                candidate += 0x10000
            last_full = candidate
            row["timestamp"] = FIT_EPOCH + timedelta(seconds=candidate)
        resolved.append(row)
    return resolved


def _extract_simple_stream(
    rows: list[dict[Any, Any]],
    timestamp_field: str,
    value_field: str,
    metric_key: str,
) -> list[HealthStreamPoint]:
    points = []
    for row in rows:
        ts = row.get(timestamp_field)
        value = _to_float(row.get(value_field))
        if isinstance(ts, datetime) and value is not None:
            points.append(HealthStreamPoint(metric_key, ts, value))
    return points


def _build_sleep(
    sleep_levels: list[dict[Any, Any]],
    event_rows: list[dict[Any, Any]],
    assessment: dict[Any, Any] | None,
    unrecognized: set[str],
) -> tuple[ParsedSleepSession, list[HealthObservation]] | None:
    valid_levels = sorted(
        (r for r in sleep_levels if isinstance(r.get("timestamp"), datetime)),
        key=lambda r: r["timestamp"],
    )
    if not valid_levels:
        return None

    start_event = next(
        (
            r["timestamp"]
            for r in event_rows
            if r.get("event_type") == "start" and isinstance(r.get("timestamp"), datetime)
        ),
        None,
    )
    first_level_ts = valid_levels[0]["timestamp"]
    start = min(start_event, first_level_ts) if start_event else first_level_ts
    end = valid_levels[-1]["timestamp"]

    stages = []
    for i, row in enumerate(valid_levels):
        stage_start = row["timestamp"]
        stage_end = valid_levels[i + 1]["timestamp"] if i + 1 < len(valid_levels) else end
        if stage_end > stage_start:
            stages.append(ParsedSleepStage(str(row["sleep_level"]), stage_start, stage_end))

    local_date = start.date().isoformat()
    overall_score = _to_float(assessment.get("overall_sleep_score")) if assessment else None
    session = ParsedSleepSession(
        local_date=local_date,
        start_time_utc=start,
        end_time_utc=end,
        total_sleep_s=(end - start).total_seconds(),
        sleep_score=overall_score,
        stages=stages,
    )

    extra_observations = []
    if assessment:
        for key, value in assessment.items():
            if key == "overall_sleep_score":
                continue  # already on the session row itself
            num = _to_float(value)
            if num is None:
                continue
            if isinstance(key, str):
                metric_key = f"sleep.{key}"
            else:
                metric_key = _metric_key("sleep_assessment_mesgs", key)
                unrecognized.add(metric_key)
                continue  # unnamed field: catalog only, no observation value
            extra_observations.append(
                HealthObservation(
                    metric_key=metric_key,
                    observed_at_utc=end,
                    local_date=local_date,
                    aggregation="daily",
                    value_num=num,
                )
            )
    return session, extra_observations


def parse_health_fit(raw_bytes: bytes) -> HealthBatch:
    """Pure parse of a non-activity (wellness/monitoring/sleep) FIT file into a `HealthBatch`."""
    stream = Stream.from_bytes_io(io.BytesIO(raw_bytes))
    decoder = Decoder(stream)
    messages, errors = decoder.read()
    if errors:
        raise ValueError(f"FIT decode errors: {errors}")

    observations: list[HealthObservation] = []
    stream_points: list[HealthStreamPoint] = []
    sleep_sessions: list[ParsedSleepSession] = []
    unrecognized: set[str] = set()

    stream_points += _extract_simple_stream(
        messages.get("stress_level_mesgs", []),
        "stress_level_time",
        "stress_level_value",
        "stress_level",
    )
    stream_points += _extract_simple_stream(
        messages.get("respiration_rate_mesgs", []),
        "timestamp",
        "respiration_rate",
        "respiration_rate",
    )
    stream_points += _extract_simple_stream(
        messages.get("spo2_data_mesgs", []), "timestamp", "reading_spo2", "spo2"
    )
    stream_points += _extract_simple_stream(
        messages.get("hrv_value_mesgs", []), "timestamp", "value", "hrv"
    )

    monitoring_rows = messages.get("monitoring_mesgs", [])
    if monitoring_rows:
        info_rows = messages.get("monitoring_info_mesgs", [])
        seed = info_rows[0].get("timestamp") if info_rows else None
        for row in _resolve_compressed_timestamps(monitoring_rows, seed):
            ts = row.get("timestamp")
            hr = _to_float(row.get("heart_rate"))
            if isinstance(ts, datetime) and hr is not None:
                stream_points.append(HealthStreamPoint("heart_rate", ts, hr))

    for row in messages.get("monitoring_hr_data_mesgs", []):
        ts = row.get("timestamp")
        rhr = _to_float(row.get("resting_heart_rate"))
        if isinstance(ts, datetime) and rhr is not None:
            observations.append(
                HealthObservation(
                    metric_key="resting_heart_rate",
                    observed_at_utc=ts,
                    local_date=ts.date().isoformat(),
                    aggregation="daily",
                    value_num=rhr,
                    unit="bpm",
                )
            )

    for row in messages.get("hrv_status_summary_mesgs", []):
        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        local_date = ts.date().isoformat()
        handled_fields = set(_HRV_STATUS_NAMED_FIELDS) | {"timestamp", "status"}
        for field_name, (metric_key, unit) in _HRV_STATUS_NAMED_FIELDS.items():
            value = _to_float(row.get(field_name))
            if value is not None:
                observations.append(
                    HealthObservation(metric_key, ts, local_date, "daily", value, unit=unit)
                )
        status = row.get("status")
        if status is not None:
            observations.append(
                HealthObservation(
                    "hrv.status", ts, local_date, "daily", None, value_text=str(status)
                )
            )
        for key, value in row.items():
            if key in handled_fields:
                continue
            if isinstance(key, str):
                num = _to_float(value)
                if num is not None:
                    key_name = _metric_key("hrv_status_summary_mesgs", key)
                    observations.append(HealthObservation(key_name, ts, local_date, "daily", num))
            else:
                unrecognized.add(_metric_key("hrv_status_summary_mesgs", key))

    sleep_levels = messages.get("sleep_level_mesgs", [])
    if sleep_levels:
        assessments = messages.get("sleep_assessment_mesgs", [])
        built = _build_sleep(
            sleep_levels,
            messages.get("event_mesgs", []),
            assessments[0] if assessments else None,
            unrecognized,
        )
        if built:
            session, extra_obs = built
            sleep_sessions.append(session)
            observations.extend(extra_obs)

    for row in messages.get("nap_event_mesgs", []):
        start = row.get("start_time")
        end = row.get("end_time")
        if isinstance(start, datetime) and isinstance(end, datetime):
            feedback = row.get("feedback")
            observations.append(
                HealthObservation(
                    metric_key="nap",
                    observed_at_utc=end,
                    local_date=start.date().isoformat(),
                    aggregation="interval",
                    value_num=(end - start).total_seconds(),
                    value_text=str(feedback) if feedback is not None else None,
                    unit="s",
                    interval_start=start,
                    interval_end=end,
                )
            )

    for message_type, rows in messages.items():
        if message_type in _HANDLED_MESSAGE_TYPES or not rows:
            continue
        for key in rows[0]:
            unrecognized.add(_metric_key(message_type, key))

    return HealthBatch(
        observations=observations,
        stream_points=stream_points,
        sleep_sessions=sleep_sessions,
        unrecognized_field_keys=sorted(unrecognized),
    )
