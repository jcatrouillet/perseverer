"""Tests for health/apple_health_parser.py, using a small synthetic export.xml fixture shaped
like a real Apple Health export (record attributes confirmed against the user's real export this
session -- not their real readings, which stay out of the repo)."""

from perseverer.health.apple_health_parser import parse_apple_health_export_xml


def _record(
    record_type: str,
    *,
    start_date: str,
    value: str,
    unit: str,
    source_name: str = "Health",
) -> str:
    return (
        f'<Record type="{record_type}" sourceName="{source_name}" sourceVersion="18.5" '
        f'unit="{unit}" creationDate="{start_date}" startDate="{start_date}" '
        f'endDate="{start_date}" value="{value}"/>'
    )


def _xml(*records: str) -> bytes:
    body = "\n".join(records)
    header = '<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_US">\n'
    return f"{header}{body}\n</HealthData>\n".encode()


WEIGHT_PRE_CUTOFF = _record(
    "HKQuantityTypeIdentifierBodyMass",
    start_date="2020-09-25 08:12:00 -0700",
    value="83.9",
    unit="kg",
    source_name="SmarTrack",
)
WEIGHT_POST_CUTOFF = _record(
    "HKQuantityTypeIdentifierBodyMass",
    start_date="2021-01-01 08:00:00 -0700",
    value="80.0",
    unit="kg",
    source_name="SmarTrack",
)
WEIGHT_EUFY_SOURCED = _record(
    "HKQuantityTypeIdentifierBodyMass",
    start_date="2020-11-11 19:32:09 -0700",
    value="83.8",
    unit="kg",
    source_name="eufy Life",
)
WEIGHT_IN_POUNDS = _record(
    "HKQuantityTypeIdentifierBodyMass",
    start_date="2015-06-01 08:00:00 -0700",
    value="185.0",
    unit="lb",
    source_name="Health",
)
BODY_FAT_FRACTION = _record(
    "HKQuantityTypeIdentifierBodyFatPercentage",
    start_date="2020-06-22 11:14:09 -0700",
    value="0.211",
    unit="%",
    source_name="SmarTrack",
)
BMI = _record(
    "HKQuantityTypeIdentifierBodyMassIndex",
    start_date="2020-06-22 11:14:09 -0700",
    value="25.5606",
    unit="count",
    source_name="SmarTrack",
)
BP_SYSTOLIC = _record(
    "HKQuantityTypeIdentifierBloodPressureSystolic",
    start_date="2025-06-15 23:02:00 -0700",
    value="124",
    unit="mmHg",
)
BP_DIASTOLIC = _record(
    "HKQuantityTypeIdentifierBloodPressureDiastolic",
    start_date="2025-06-15 23:02:00 -0700",
    value="66",
    unit="mmHg",
)
UNRELATED_HEART_RATE = _record(
    "HKQuantityTypeIdentifierHeartRate",
    start_date="2025-06-15 23:02:00 -0700",
    value="72",
    unit="count/min",
)
MALFORMED_WEIGHT = _record(
    "HKQuantityTypeIdentifierBodyMass",
    start_date="2018-01-01 08:00:00 -0700",
    value="not-a-number",
    unit="kg",
    source_name="Health",
)

CUTOFF = "2020-11-12"


def test_pre_cutoff_weight_is_imported() -> None:
    batch = parse_apple_health_export_xml(_xml(WEIGHT_PRE_CUTOFF), weight_before=CUTOFF)
    keys = {o.metric_key for o in batch.observations}
    assert "apple_health.body_mass" in keys
    obs = next(o for o in batch.observations if o.metric_key == "apple_health.body_mass")
    assert obs.value_num == 83.9
    assert obs.unit == "kg"
    assert obs.local_date == "2020-09-25"
    assert obs.aggregation == "instant"


def test_post_cutoff_weight_is_excluded() -> None:
    batch = parse_apple_health_export_xml(_xml(WEIGHT_POST_CUTOFF), weight_before=CUTOFF)
    assert batch.observations == []


def test_eufy_sourced_weight_is_excluded_even_though_it_is_before_cutoff() -> None:
    batch = parse_apple_health_export_xml(_xml(WEIGHT_EUFY_SOURCED), weight_before=CUTOFF)
    assert batch.observations == []


def test_weight_in_pounds_is_converted_to_kg() -> None:
    batch = parse_apple_health_export_xml(_xml(WEIGHT_IN_POUNDS), weight_before=CUTOFF)
    obs = next(o for o in batch.observations if o.metric_key == "apple_health.body_mass")
    assert obs.value_num is not None
    assert round(obs.value_num, 2) == round(185.0 * 0.45359237, 2)
    assert obs.unit == "kg"


def test_body_fat_fraction_is_converted_to_true_percent() -> None:
    batch = parse_apple_health_export_xml(_xml(BODY_FAT_FRACTION), weight_before=CUTOFF)
    obs = next(o for o in batch.observations if o.metric_key == "apple_health.body_fat_percentage")
    assert obs.value_num is not None
    assert round(obs.value_num, 1) == 21.1
    assert obs.unit == "%"


def test_bmi_passthrough_with_no_unit() -> None:
    batch = parse_apple_health_export_xml(_xml(BMI), weight_before=CUTOFF)
    obs = next(o for o in batch.observations if o.metric_key == "apple_health.body_mass_index")
    assert obs.value_num is not None
    assert round(obs.value_num, 4) == 25.5606
    assert obs.unit is None


def test_blood_pressure_systolic_and_diastolic_both_imported_as_independent_observations() -> None:
    batch = parse_apple_health_export_xml(_xml(BP_SYSTOLIC, BP_DIASTOLIC), weight_before=CUTOFF)
    by_key = {o.metric_key: o for o in batch.observations}
    assert by_key["apple_health.blood_pressure_systolic"].value_num == 124
    assert by_key["apple_health.blood_pressure_diastolic"].value_num == 66
    assert by_key["apple_health.blood_pressure_systolic"].unit == "mmHg"
    assert (
        by_key["apple_health.blood_pressure_systolic"].observed_at_utc
        == by_key["apple_health.blood_pressure_diastolic"].observed_at_utc
    )


def test_blood_pressure_is_not_gated_by_weight_before() -> None:
    batch = parse_apple_health_export_xml(_xml(BP_SYSTOLIC, BP_DIASTOLIC), weight_before=None)
    keys = {o.metric_key for o in batch.observations}
    assert "apple_health.blood_pressure_systolic" in keys
    assert "apple_health.blood_pressure_diastolic" in keys


def test_weight_before_none_skips_all_body_composition() -> None:
    batch = parse_apple_health_export_xml(
        _xml(WEIGHT_PRE_CUTOFF, BODY_FAT_FRACTION, BMI), weight_before=None
    )
    assert batch.observations == []


def test_unrelated_record_types_are_ignored() -> None:
    batch = parse_apple_health_export_xml(_xml(UNRELATED_HEART_RATE), weight_before=CUTOFF)
    assert batch.observations == []


def test_malformed_value_is_skipped_not_fatal() -> None:
    batch = parse_apple_health_export_xml(
        _xml(MALFORMED_WEIGHT, WEIGHT_PRE_CUTOFF), weight_before=CUTOFF
    )
    keys = {o.metric_key for o in batch.observations}
    assert keys == {"apple_health.body_mass"}
    assert len(batch.observations) == 1


def test_local_date_and_observed_at_utc_derived_from_non_utc_offset() -> None:
    # 2020-09-25 08:12:00 -0700 == 2020-09-25 15:12:00 UTC -- same calendar date either way here,
    # so also check a case where the offset actually shifts the UTC calendar date.
    late_local = _record(
        "HKQuantityTypeIdentifierBodyMass",
        start_date="2020-09-25 23:00:00 -0700",
        value="83.0",
        unit="kg",
        source_name="Health",
    )
    batch = parse_apple_health_export_xml(_xml(late_local), weight_before=CUTOFF)
    obs = batch.observations[0]
    assert obs.observed_at_utc.isoformat() == "2020-09-26T06:00:00"
    assert obs.local_date == "2020-09-26"
