"""Pure streaming parser for an Apple Health "export.xml" archive.

Extracts exactly five `<Record type="...">` types out of the ~76 present in a real export --
blood pressure (full history, no existing source anywhere else in this project) and body
mass/BMI/body-fat percentage from *before* the athlete's Eufy scale (see `weight_before`),
which fills a real gap since Eufy's own earliest reading only goes back to when that scale was
bought. Everything else in the file (activity/workout data, nutrition, mindfulness, ECG,
clinical records, Apple-Watch-era vitals Garmin already covers) is deliberately not parsed here
-- a scope decision, not data loss, since the caller (`adapters/apple_health_export.py`) archives
the *entire* file verbatim before this function ever runs, so extending this parser later to
pull more record types never requires the user to re-supply the original export.

Uses `xml.etree.ElementTree.iterparse` (`events=("end",)`, clearing each element immediately
after reading it) rather than `ET.fromstring` -- the only streaming-XML code in this repo, a
deliberate departure from `gpx/parser.py`/`tcx/parser.py`'s non-streaming convention, justified
by a real export.xml running to gigabytes (unlike any GPX/TCX file this codebase otherwise
parses).
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from perseverer.health.types import HealthBatch, HealthObservation

# Apple's own HKQuantityTypeIdentifier -> this project's metric_key namespace.
_WANTED_TYPES: dict[str, str] = {
    "HKQuantityTypeIdentifierBodyMass": "apple_health.body_mass",
    "HKQuantityTypeIdentifierBodyMassIndex": "apple_health.body_mass_index",
    "HKQuantityTypeIdentifierBodyFatPercentage": "apple_health.body_fat_percentage",
    "HKQuantityTypeIdentifierBloodPressureSystolic": "apple_health.blood_pressure_systolic",
    "HKQuantityTypeIdentifierBloodPressureDiastolic": "apple_health.blood_pressure_diastolic",
}

# Gated by weight_before + the eufy-source exclusion below; blood pressure is neither.
_BODY_COMPOSITION_TYPES = frozenset(
    {
        "HKQuantityTypeIdentifierBodyMass",
        "HKQuantityTypeIdentifierBodyMassIndex",
        "HKQuantityTypeIdentifierBodyFatPercentage",
    }
)

# Apple's own sync of this project's Eufy adapter data -- excluded even when its date falls
# before `weight_before`, since it's the exact same reading the eufy adapter already ingests,
# just landing on the other side of a UTC/local-date boundary (confirmed against real data: one
# such record is dated 2020-11-11 in Apple Health, one day before the eufy adapter's own earliest
# raw fetch of 2020-11-12 UTC).
_EUFY_SOURCE_NAME = "eufy Life"

_LB_TO_KG = 0.45359237

# Apple's own export.xml datetime format, e.g. "2020-09-25 08:12:00 -0700".
_APPLE_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S %z"


def _parse_observed_at_utc(start_date: str | None) -> tuple[datetime, str] | None:
    if not start_date:
        return None
    try:
        parsed = datetime.strptime(start_date, _APPLE_DATETIME_FORMAT)
    except ValueError:
        return None
    observed_at_utc = parsed.astimezone(UTC).replace(tzinfo=None)
    # Naive UTC-date truncation, matching eufy_parser.py's own convention -- health data
    # deliberately doesn't apply the offset-adjusted conversion activity.local_date uses (ADR
    # 0009).
    local_date = observed_at_utc.date().isoformat()
    return observed_at_utc, local_date


def _normalize_value(
    record_type: str, value_raw: str | None, unit: str | None
) -> tuple[float, str | None] | None:
    try:
        value = float(value_raw) if value_raw is not None else None
    except ValueError:
        return None
    if value is None:
        return None

    if record_type == "HKQuantityTypeIdentifierBodyMass":
        if unit == "lb" or unit == "lbs":
            return value * _LB_TO_KG, "kg"
        if unit == "kg" or unit is None:
            return value, "kg"
        # Defensive: an unrecognized unit is stored unconverted with its own unit string faithfully
        # rather than silently mislabeled as kg -- not exercised by the real export (100% kg there).
        return value, unit
    if record_type == "HKQuantityTypeIdentifierBodyMassIndex":
        return value, None
    if record_type == "HKQuantityTypeIdentifierBodyFatPercentage":
        # Apple's own HealthKit convention: stored as a 0-1 fraction despite carrying unit="%"
        # (confirmed against real data: value="0.211" for a ~21% reading) -- normalized to a true
        # percent to match eufy.scale.body_fat's already-established convention (e.g. 19.4).
        return value * 100, "%"
    # Blood pressure: passthrough, always mmHg in the real export.
    return value, unit


def parse_apple_health_export_xml(xml_bytes: bytes, *, weight_before: str | None) -> HealthBatch:
    """`weight_before`: an ISO date string -- only BodyMass/BodyMassIndex/BodyFatPercentage
    records strictly before this local date are imported (and never any sourceName="eufy Life"
    record, regardless of date). `None` skips all three body-composition types entirely but still
    imports the full blood-pressure history, which isn't gated by any cutoff.
    """
    observations: list[HealthObservation] = []

    context = ET.iterparse(io.BytesIO(xml_bytes), events=("end",))
    for _, elem in context:
        if elem.tag != "Record":
            continue
        record_type = elem.get("type")
        if record_type not in _WANTED_TYPES:
            elem.clear()
            continue

        start_date = elem.get("startDate")
        source_name = elem.get("sourceName")
        value_raw = elem.get("value")
        unit = elem.get("unit")
        elem.clear()

        if record_type in _BODY_COMPOSITION_TYPES and (
            weight_before is None or source_name == _EUFY_SOURCE_NAME
        ):
            continue

        parsed_date = _parse_observed_at_utc(start_date)
        if parsed_date is None:
            continue
        observed_at_utc, local_date = parsed_date

        if (
            record_type in _BODY_COMPOSITION_TYPES
            and weight_before is not None
            and local_date >= weight_before
        ):
            continue

        normalized = _normalize_value(record_type, value_raw, unit)
        if normalized is None:
            continue
        value_num, out_unit = normalized

        observations.append(
            HealthObservation(
                metric_key=_WANTED_TYPES[record_type],
                observed_at_utc=observed_at_utc,
                local_date=local_date,
                aggregation="instant",
                value_num=value_num,
                unit=out_unit,
            )
        )

    return HealthBatch(observations=observations)
