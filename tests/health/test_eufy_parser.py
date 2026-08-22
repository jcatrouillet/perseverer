"""Tests for health/eufy_parser.py, using a small synthetic payload shaped like a real Eufy
Life `last_device_data` record (field names/structure confirmed against the live API this
session -- not the user's real readings, which stay out of the repo).
"""

import json
from typing import cast

import pytest

from perseverer.health.eufy_parser import parse_eufy_scale_reading

RECORD = {
    "id": "27A67DD8-7E14-4ADE-8106-0C4116F05C58",
    "device_id": "some-device-id",
    "user_id": "2602530",
    "customer_id": "some-customer-id",
    "group_id": "",
    "create_time": 1717200000,  # 2024-06-01T00:00:00Z
    "update_time": 1717200010,
    "scale_data": {
        "mode": 0,
        "weight": 772,  # hectograms -- 77.2kg
        "standard_weight": 0,
        "water_weight": 0,
        "bmi": 23.1,
        "body_fat": 19.4,
        "subcutaneous_fat_rate": 0,
        "visceral_fat_index": 0,
        "muscle": 74.4,
        "muscle_mass": 57.4,
        "skeletal_muscle_mass": 0,
        "bmr": 1456,
        "bone": 4,
        "bone_mass": 3.1,
        "water": 53.8,
        "body_age": 47,
        "protein_ratio": 16.3,
        "impedance": 12580857,
        "visceral_fat": 13,
        "fat_free_weight": 15,
        "body_fat_mass": 15,
        "fat_mode": 0,
        "heart_rate": 0,
        "body_type": 0,
        "head_size": 0,
        "height": 0,
    },
    "status": 0,
    "product_code": "eufy T9147",
}


def _bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def test_weight_is_converted_from_hectograms_to_kg() -> None:
    batch = parse_eufy_scale_reading(_bytes(RECORD))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["eufy.scale.weight"].value_num == 77.2
    assert obs["eufy.scale.weight"].unit == "kg"


def test_percent_fields_get_a_percent_unit_everything_else_is_unconverted() -> None:
    batch = parse_eufy_scale_reading(_bytes(RECORD))
    obs = {o.metric_key: o for o in batch.observations}

    assert obs["eufy.scale.body_fat"].value_num == 19.4
    assert obs["eufy.scale.body_fat"].unit == "%"
    assert obs["eufy.scale.muscle"].unit == "%"
    assert obs["eufy.scale.bone"].unit == "%"
    assert obs["eufy.scale.water"].unit == "%"
    assert obs["eufy.scale.protein_ratio"].unit == "%"

    # Everything else: stored exactly as reported, no fabricated unit.
    assert obs["eufy.scale.bmi"].value_num == 23.1
    assert obs["eufy.scale.bmi"].unit is None
    assert obs["eufy.scale.muscle_mass"].value_num == 57.4
    assert obs["eufy.scale.muscle_mass"].unit is None
    assert obs["eufy.scale.impedance"].value_num == 12580857.0
    assert obs["eufy.scale.impedance"].unit is None


def test_really_everything_all_scale_data_fields_become_observations() -> None:
    """The whole point of this parser: don't just pull the 9 fields the sibling project does --
    every scalar field in scale_data becomes an observation."""
    batch = parse_eufy_scale_reading(_bytes(RECORD))
    keys = {o.metric_key for o in batch.observations}
    scale_data = cast("dict[str, object]", RECORD["scale_data"])
    for field in scale_data:
        assert f"eufy.scale.{field}" in keys, f"missing {field}"


def test_product_code_is_captured_as_text() -> None:
    batch = parse_eufy_scale_reading(_bytes(RECORD))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["eufy.scale.product_code"].value_text == "eufy T9147"
    assert obs["eufy.scale.product_code"].value_num is None


def test_status_becomes_a_reading_level_observation() -> None:
    batch = parse_eufy_scale_reading(_bytes(RECORD))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["eufy.reading.status"].value_num == 0.0


def test_identifiers_are_skipped_not_stored_as_observations() -> None:
    batch = parse_eufy_scale_reading(_bytes(RECORD))
    keys = {o.metric_key for o in batch.observations}
    assert "eufy.reading.id" not in keys
    assert "eufy.reading.device_id" not in keys
    assert "eufy.reading.user_id" not in keys
    assert "eufy.reading.customer_id" not in keys


def test_local_date_and_timestamp_derived_from_create_time() -> None:
    batch = parse_eufy_scale_reading(_bytes(RECORD))
    assert batch.observations[0].local_date == "2024-06-01"
    assert all(o.local_date == "2024-06-01" for o in batch.observations)


def test_aggregation_is_instant_not_daily() -> None:
    # A weigh-in is a point-in-time reading, not a pre-aggregated daily summary -- distinct from
    # the Garmin JSON parsers' "daily" convention.
    batch = parse_eufy_scale_reading(_bytes(RECORD))
    assert all(o.aggregation == "instant" for o in batch.observations)


def test_null_and_boolean_fields_are_skipped_without_crashing() -> None:
    record: dict[str, object] = dict(RECORD)
    scale_data = cast("dict[str, object]", RECORD["scale_data"])
    record["scale_data"] = {**scale_data, "weight": None, "some_flag": True}
    batch = parse_eufy_scale_reading(_bytes(record))
    keys = {o.metric_key for o in batch.observations}
    assert "eufy.scale.weight" not in keys
    assert "eufy.scale.some_flag" not in keys


def test_raises_on_missing_create_time() -> None:
    record = {k: v for k, v in RECORD.items() if k != "create_time"}
    with pytest.raises(ValueError, match="create_time"):
        parse_eufy_scale_reading(_bytes(record))
