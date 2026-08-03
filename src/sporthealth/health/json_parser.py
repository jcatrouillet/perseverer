"""Pure JSON parsing for Garmin Connect-shaped daily-summary/hydration files.

Real samples confirmed the schema matches Garmin Connect's own daily-summary API response
shape closely (camelCase fields: `totalSteps`, `restingHeartRate`, `bodyBatteryChargedValue`,
`averageSpo2`, sleep durations, etc.) — see docs/adr/0004-phase-2-health-ingestion.md.

Top-level scalar fields become individual `health_observation` rows keyed
`garmin.daily_summary.<field>` / `garmin.hydration.<field>`. Nested objects/lists (event
lists, the `rule` object, etc.) are deliberately NOT flattened into observations — the raw
JSON bytes are archived regardless (raw-first holds), and the field's existence is still
registered via `unrecognized_field_keys` so it's visible, not invisible, pending a decision on
whether it's worth promoting to first-class later.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sporthealth.health.types import HealthBatch, HealthObservation

# Identifiers, not health facts — excluded from becoming observations.
_SKIP_FIELDS = frozenset({"userProfileId", "userId", "userDailySummaryId", "uuid"})


def _parse_garmin_datetime(value: str) -> datetime:
    """Garmin's own JSON timestamps look like "2025-01-02T08:00:00.0" — naive per this
    project's naive-implicitly-UTC convention (db/schema.py); the "Gmt"-suffixed fields these
    come from are already UTC instants, so no conversion is needed, just cleanup.
    """
    cleaned = value.replace("Z", "")
    if "." in cleaned:
        cleaned = cleaned.split(".")[0]
    return datetime.fromisoformat(cleaned)


def _parse_generic(
    raw_bytes: bytes, *, key_prefix: str, date_field: str, anchor_field: str | None
) -> HealthBatch:
    data: dict[str, Any] = json.loads(raw_bytes)
    local_date = data.get(date_field)
    if not isinstance(local_date, str):
        raise ValueError(f"missing/invalid {date_field!r} in {key_prefix} JSON")

    if anchor_field and isinstance(data.get(anchor_field), str):
        anchor = _parse_garmin_datetime(data[anchor_field])
    else:
        anchor = datetime.fromisoformat(local_date)  # midnight — best available fallback

    observations = []
    unrecognized = []
    for key, value in data.items():
        if key in _SKIP_FIELDS or key == date_field:
            continue
        if value is None or isinstance(value, bool):
            continue  # nothing meaningful to store as a value
        if isinstance(value, int | float):
            observations.append(
                HealthObservation(
                    metric_key=f"{key_prefix}.{key}",
                    observed_at_utc=anchor,
                    local_date=local_date,
                    aggregation="daily",
                    value_num=float(value),
                )
            )
        elif isinstance(value, str):
            observations.append(
                HealthObservation(
                    metric_key=f"{key_prefix}.{key}",
                    observed_at_utc=anchor,
                    local_date=local_date,
                    aggregation="daily",
                    value_num=None,
                    value_text=value,
                )
            )
        else:
            unrecognized.append(f"{key_prefix}.{key}")  # dict/list — see module docstring

    return HealthBatch(observations=observations, unrecognized_field_keys=sorted(set(unrecognized)))


def parse_daily_summary_json(raw_bytes: bytes) -> HealthBatch:
    return _parse_generic(
        raw_bytes,
        key_prefix="garmin.daily_summary",
        date_field="calendarDate",
        anchor_field="wellnessEndTimeGmt",
    )


def parse_hydration_json(raw_bytes: bytes) -> HealthBatch:
    return _parse_generic(
        raw_bytes, key_prefix="garmin.hydration", date_field="calendarDate", anchor_field=None
    )
