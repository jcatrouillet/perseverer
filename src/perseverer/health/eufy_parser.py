"""Pure JSON parsing for a single Eufy Life smart-scale reading (one record from
`GET /v1/device/last_device_data`).

Confirmed against the real, live Eufy Life API (not assumed): the endpoint's `limit` query
param is silently ignored -- it always returns the athlete's entire history in one call (539
records, 2020-11-12 through today, for the account this was verified against). Each record's
`scale_data` sub-object carries ~24 fields; an existing Eufy sync tool (which
uploads this same data into Garmin/intervals.icu) only extracts 9 of them. This parser
generically flattens every scalar field in `scale_data` into an `eufy.scale.<field>`
observation -- "really everything", matching this project's own "never drop an unknown field"
mandate -- rather than hand-picking the same narrow subset.

Unit handling is deliberately conservative. Only `weight` gets a confirmed unit conversion:
Eufy's raw value is in hectograms (`772` -> `77.2kg`), the same factor that tool
already uses in production, independently cross-validated here against two other fields in the
same real sample: `muscle_mass` (57.4) / 77.2 = 74.3%, matching the separate `muscle` percentage
field (74.4) almost exactly; `bone_mass` (3.1) / 77.2 = 4.0%, matching `bone` (4) exactly. Every
other field's scale is genuinely ambiguous from a single sample (e.g. `fat_free_weight` and
`body_fat_mass` read identically in the probe, which doesn't cleanly resolve either interpretation)
-- those are stored exactly as reported, unconverted, rather than guessed at.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from perseverer.health.types import HealthBatch, HealthObservation

# Identifiers/provenance, not health facts -- excluded from becoming observations, matching
# json_parser.py's own _SKIP_FIELDS precedent for the same class of field.
_SKIP_FIELDS = frozenset({"id", "device_id", "user_id", "customer_id", "group_id"})

# scale_data fields confirmed (see module docstring) to be plain percentages already -- safe to
# label, unlike the majority of fields whose scale isn't independently verifiable from one
# sample.
_PERCENT_FIELDS = frozenset({"body_fat", "muscle", "bone", "water", "protein_ratio"})

# The one field with a confirmed, cross-validated unit conversion.
_WEIGHT_FIELD = "weight"
_WEIGHT_DIVISOR = 10.0


def parse_eufy_scale_reading(raw_bytes: bytes) -> HealthBatch:
    """One archived Eufy reading into `eufy.scale.<field>` observations -- every scalar field, not
    just the ones the app charts.
    """
    record: dict[str, Any] = json.loads(raw_bytes)

    create_time = record.get("create_time")
    if not isinstance(create_time, int | float):
        raise ValueError(f"missing/invalid create_time in Eufy record: {record}")
    observed_at_utc = datetime.fromtimestamp(create_time, tz=UTC).replace(tzinfo=None)
    # Naive UTC-date truncation, matching every other health parser's local_date convention in
    # this codebase (health data deliberately doesn't apply the offset-adjusted conversion
    # activity.local_date uses -- see the design doc's documented activity-vs-health distinction).
    local_date = observed_at_utc.date().isoformat()

    observations: list[HealthObservation] = []
    unrecognized: list[str] = []

    scale_data = record.get("scale_data")
    if isinstance(scale_data, dict):
        for key, value in scale_data.items():
            if value is None or isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                unit = "kg" if key == _WEIGHT_FIELD else ("%" if key in _PERCENT_FIELDS else None)
                value_num = float(value) / _WEIGHT_DIVISOR if key == _WEIGHT_FIELD else float(value)
                observations.append(
                    HealthObservation(
                        metric_key=f"eufy.scale.{key}",
                        observed_at_utc=observed_at_utc,
                        local_date=local_date,
                        aggregation="instant",
                        value_num=value_num,
                        unit=unit,
                    )
                )
            else:
                unrecognized.append(f"eufy.scale.{key}")

    product_code = record.get("product_code")
    if isinstance(product_code, str) and product_code:
        observations.append(
            HealthObservation(
                metric_key="eufy.scale.product_code",
                observed_at_utc=observed_at_utc,
                local_date=local_date,
                aggregation="instant",
                value_num=None,
                value_text=product_code,
            )
        )

    for key, value in record.items():
        if key in _SKIP_FIELDS or key in ("scale_data", "product_code", "create_time"):
            continue
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            observations.append(
                HealthObservation(
                    metric_key=f"eufy.reading.{key}",
                    observed_at_utc=observed_at_utc,
                    local_date=local_date,
                    aggregation="instant",
                    value_num=float(value),
                )
            )
        elif isinstance(value, str) and value:
            observations.append(
                HealthObservation(
                    metric_key=f"eufy.reading.{key}",
                    observed_at_utc=observed_at_utc,
                    local_date=local_date,
                    aggregation="instant",
                    value_num=None,
                    value_text=value,
                )
            )
        else:
            unrecognized.append(f"eufy.reading.{key}")

    return HealthBatch(observations=observations, unrecognized_field_keys=sorted(set(unrecognized)))
