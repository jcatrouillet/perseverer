"""Tests for health/json_parser.py, using small redacted/synthetic JSON payloads shaped like
Garmin Connect's daily-summary/hydration API responses -- not the user's real files, which
stay out of the repo (see docs/adr/0004-phase-2-health-ingestion.md).
"""

import json
from datetime import datetime

import pytest

from sporthealth.health.json_parser import parse_daily_summary_json, parse_hydration_json

DAILY_SUMMARY = {
    "userProfileId": 123456,
    "calendarDate": "2025-06-01",
    "wellnessEndTimeGmt": "2025-06-02T06:00:00.0",
    "totalSteps": 8421,
    "restingHeartRate": 48,
    "bodyBatteryChargedValue": 55,
    "averageSpo2": 97.5,
    "minHeartRate": 45,
    "highlyActive": True,
    "bodyBatteryDynamicFeedbackEvent": {"eventTimestampGmt": "2025-06-01T12:00:00.0"},
    "bodyBatteryActivityEventList": [{"eventType": "sleep"}],
}

HYDRATION = {
    "userId": 123456,
    "calendarDate": "2025-06-01",
    "valueInML": 1500.0,
    "goalInML": 2000.0,
}


def _bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def test_daily_summary_scalar_fields_become_observations() -> None:
    batch = parse_daily_summary_json(_bytes(DAILY_SUMMARY))
    obs = {o.metric_key: o for o in batch.observations}

    assert obs["garmin.daily_summary.totalSteps"].value_num == 8421.0
    assert obs["garmin.daily_summary.restingHeartRate"].value_num == 48.0
    assert obs["garmin.daily_summary.bodyBatteryChargedValue"].value_num == 55.0
    assert obs["garmin.daily_summary.averageSpo2"].value_num == 97.5


def test_daily_summary_anchors_observations_at_wellness_end_time() -> None:
    batch = parse_daily_summary_json(_bytes(DAILY_SUMMARY))
    obs = next(o for o in batch.observations if o.metric_key == "garmin.daily_summary.totalSteps")
    assert obs.observed_at_utc == datetime(2025, 6, 2, 6, 0, 0)
    assert obs.local_date == "2025-06-01"
    assert obs.aggregation == "daily"


def test_daily_summary_identifier_fields_are_skipped() -> None:
    batch = parse_daily_summary_json(_bytes(DAILY_SUMMARY))
    keys = {o.metric_key for o in batch.observations}
    assert "garmin.daily_summary.userProfileId" not in keys
    assert "garmin.daily_summary.calendarDate" not in keys


def test_daily_summary_booleans_are_skipped_entirely() -> None:
    """Not an observation, and not cataloged as unrecognized either -- there's nothing
    meaningful to store or flag about a boolean flag field."""
    batch = parse_daily_summary_json(_bytes(DAILY_SUMMARY))
    keys = {o.metric_key for o in batch.observations}
    assert "garmin.daily_summary.highlyActive" not in keys
    assert "garmin.daily_summary.highlyActive" not in batch.unrecognized_field_keys


def test_daily_summary_nested_values_are_cataloged_not_flattened() -> None:
    """Nested dict/list values are never turned into observations (ADR 0004 decision 5), but
    their existence is still registered so they're visible, not silently dropped."""
    batch = parse_daily_summary_json(_bytes(DAILY_SUMMARY))
    keys = {o.metric_key for o in batch.observations}
    assert "garmin.daily_summary.bodyBatteryDynamicFeedbackEvent" not in keys
    assert "garmin.daily_summary.bodyBatteryActivityEventList" not in keys
    assert "garmin.daily_summary.bodyBatteryDynamicFeedbackEvent" in batch.unrecognized_field_keys
    assert "garmin.daily_summary.bodyBatteryActivityEventList" in batch.unrecognized_field_keys


def test_daily_summary_missing_calendar_date_raises() -> None:
    with pytest.raises(ValueError, match="calendarDate"):
        parse_daily_summary_json(_bytes({"totalSteps": 100}))


def test_hydration_scalar_fields_become_observations() -> None:
    batch = parse_hydration_json(_bytes(HYDRATION))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["garmin.hydration.valueInML"].value_num == 1500.0
    assert obs["garmin.hydration.goalInML"].value_num == 2000.0


def test_hydration_has_no_anchor_field_falls_back_to_midnight() -> None:
    """Hydration JSON (unlike daily_summary) has no timestamp field to anchor to, so
    observed_at_utc falls back to midnight of calendarDate."""
    batch = parse_hydration_json(_bytes(HYDRATION))
    obs = next(o for o in batch.observations if o.metric_key == "garmin.hydration.valueInML")
    assert obs.observed_at_utc == datetime(2025, 6, 1, 0, 0, 0)
    assert obs.local_date == "2025-06-01"
