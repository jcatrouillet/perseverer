"""Tests for health/json_parser.py, using small redacted/synthetic JSON payloads shaped like
Garmin Connect's daily-summary/hydration API responses -- not the user's real files, which
stay out of the repo (see docs/adr/0004-phase-2-health-ingestion.md).
"""

import json
from datetime import UTC, datetime

import pytest

from perseverer.health.json_parser import (
    parse_daily_hrv_json,
    parse_daily_race_predictions_json,
    parse_daily_sleep_json,
    parse_daily_summary_json,
    parse_daily_training_readiness_json,
    parse_daily_training_status_json,
    parse_garmin_export_json,
    parse_hydration_json,
)

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


# --- parse_garmin_export_json: GDPR export's day/event-record-array JSON shape (ADR 0005) ----

SLEEP_DATA_RECORDS: list[dict[str, object]] = [
    {
        "userProfilePK": 87061520,
        "deviceId": 3427744459,
        "calendarDate": "2023-01-12",
        "sleepEndTimestampGMT": "2023-01-12T15:31:00.0",
        "deepSleepSeconds": 4320,
        "sleepScores": {"overallScore": 57},
    },
    {
        "userProfilePK": 87061520,
        "calendarDate": "2023-01-13",
        "sleepEndTimestampGMT": "2023-01-13T14:00:00.0",
        "deepSleepSeconds": 3600,
    },
    {"userProfilePK": 87061520, "deepSleepSeconds": 9999},  # missing calendarDate
]


def _list_bytes(records: list[dict[str, object]]) -> bytes:
    return json.dumps(records).encode("utf-8")


def test_export_json_flattens_scalars_with_report_kind_namespace() -> None:
    batch = parse_garmin_export_json(_list_bytes(SLEEP_DATA_RECORDS), report_kind="sleepData")
    values = {
        (o.metric_key, o.local_date): o.value_num
        for o in batch.observations
        if o.metric_key == "garmin.export.sleepData.deepSleepSeconds"
    }
    assert values[("garmin.export.sleepData.deepSleepSeconds", "2023-01-12")] == 4320.0
    assert values[("garmin.export.sleepData.deepSleepSeconds", "2023-01-13")] == 3600.0


def test_export_json_anchors_using_priority_field_list() -> None:
    batch = parse_garmin_export_json(_list_bytes(SLEEP_DATA_RECORDS), report_kind="sleepData")
    obs = next(
        o
        for o in batch.observations
        if o.metric_key == "garmin.export.sleepData.deepSleepSeconds"
        and o.local_date == "2023-01-12"
    )
    assert obs.observed_at_utc == datetime(2023, 1, 12, 15, 31, 0)


def test_export_json_record_missing_calendar_date_is_skipped_not_fatal() -> None:
    """The third record has no calendarDate -- it must not crash the whole file, and the other
    two valid records' data must still come through."""
    batch = parse_garmin_export_json(_list_bytes(SLEEP_DATA_RECORDS), report_kind="sleepData")
    values = {
        o.value_num
        for o in batch.observations
        if o.metric_key == "garmin.export.sleepData.deepSleepSeconds"
    }
    assert values == {3600.0, 4320.0}


def test_export_json_identifiers_and_nested_values_excluded() -> None:
    batch = parse_garmin_export_json(_list_bytes(SLEEP_DATA_RECORDS), report_kind="sleepData")
    keys = {o.metric_key for o in batch.observations}
    assert "garmin.export.sleepData.userProfilePK" not in keys
    assert "garmin.export.sleepData.deviceId" not in keys
    assert "garmin.export.sleepData.calendarDate" not in keys
    assert "garmin.export.sleepData.sleepScores" not in keys
    assert "garmin.export.sleepData.sleepScores" in batch.unrecognized_field_keys


def test_export_json_accepts_a_single_bare_record() -> None:
    """Defensive: some report kinds might export a single dict rather than a list."""
    single = {"calendarDate": "2023-01-12", "timestamp": "2023-01-12T10:00:00.0", "score": 42}
    batch = parse_garmin_export_json(_bytes(single), report_kind="TrainingReadinessDTO")
    obs = next(o for o in batch.observations if o.metric_key.endswith("score"))
    assert obs.value_num == 42.0
    assert obs.observed_at_utc == datetime(2023, 1, 12, 10, 0, 0)


# --- parse_daily_sleep_json: garmin_connect's live get_sleep_data() shape (previously never
# fetched by this adapter at all -- see adapters/garmin_connect.py) --------------------------

SLEEP_START = datetime(2025, 6, 1, 23, 0, 0, tzinfo=UTC)
SLEEP_END = datetime(2025, 6, 2, 7, 0, 0, tzinfo=UTC)


def _epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


DAILY_SLEEP: dict[str, object] = {
    "dailySleepDTO": {
        "id": 1785573237000,
        "userProfilePK": 87061520,
        "deviceId": 3492916187,
        "calendarDate": "2025-06-01",
        "sleepStartTimestampGMT": _epoch_ms(SLEEP_START),
        "sleepEndTimestampGMT": _epoch_ms(SLEEP_END),
        "sleepTimeSeconds": 28800,
        "deepSleepSeconds": 3600,
        "lightSleepSeconds": 21600,
        "remSleepSeconds": 3600,
        "awakeSleepSeconds": 0,
        "averageRespirationValue": 14.5,
        "avgHeartRate": 50.0,
        "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}},
        "sleepNeed": {"baseline": 470},
    },
    # Real-account durations confirmed by summing each activityLevel's own segment durations and
    # checking they exactly equal dailySleepDTO's deep/light/rem/awakeSleepSeconds totals --
    # 0=deep, 1=light, 2=rem, 3=awake.
    "sleepLevels": [
        {
            "startGMT": "2025-06-01T23:00:00.0",
            "endGMT": "2025-06-02T00:00:00.0",
            "activityLevel": 1.0,
        },
        {
            "startGMT": "2025-06-02T00:00:00.0",
            "endGMT": "2025-06-02T01:00:00.0",
            "activityLevel": 0.0,
        },
    ],
}


def test_daily_sleep_builds_a_sleep_session() -> None:
    batch = parse_daily_sleep_json(_bytes(DAILY_SLEEP))
    assert len(batch.sleep_sessions) == 1
    session = batch.sleep_sessions[0]
    assert session.local_date == "2025-06-01"
    assert session.start_time_utc == SLEEP_START.replace(tzinfo=None)
    assert session.end_time_utc == SLEEP_END.replace(tzinfo=None)
    assert session.total_sleep_s == 28800.0
    assert session.sleep_score == 82.0


def test_daily_sleep_stages_mapped_from_activity_level() -> None:
    batch = parse_daily_sleep_json(_bytes(DAILY_SLEEP))
    stages = sorted(batch.sleep_sessions[0].stages, key=lambda s: s.start_time_utc)
    assert [s.stage for s in stages] == ["light", "deep"]
    assert stages[0].start_time_utc == datetime(2025, 6, 1, 23, 0, 0)
    assert stages[0].end_time_utc == datetime(2025, 6, 2, 0, 0, 0)


def test_daily_sleep_scalar_fields_become_observations() -> None:
    batch = parse_daily_sleep_json(_bytes(DAILY_SLEEP))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["garmin.daily_sleep.averageRespirationValue"].value_num == 14.5
    assert obs["garmin.daily_sleep.avgHeartRate"].value_num == 50.0
    assert obs["garmin.daily_sleep.deepSleepSeconds"].value_num == 3600.0


def test_daily_sleep_identifiers_and_nested_values_excluded() -> None:
    batch = parse_daily_sleep_json(_bytes(DAILY_SLEEP))
    keys = {o.metric_key for o in batch.observations}
    assert "garmin.daily_sleep.userProfilePK" not in keys
    assert "garmin.daily_sleep.deviceId" not in keys
    assert "garmin.daily_sleep.calendarDate" not in keys
    assert "garmin.daily_sleep.sleepScores" not in keys
    assert "garmin.daily_sleep.sleepScores" in batch.unrecognized_field_keys
    assert "garmin.daily_sleep.sleepNeed" in batch.unrecognized_field_keys


def test_daily_sleep_missing_timestamps_produces_empty_batch() -> None:
    """A day with nothing tracked yet: dailySleepDTO is present but its start/end are null --
    must not fabricate a zero-length session."""
    payload: dict[str, object] = {
        "dailySleepDTO": {
            "calendarDate": "2025-06-05",
            "sleepStartTimestampGMT": None,
            "sleepEndTimestampGMT": None,
        }
    }
    batch = parse_daily_sleep_json(_bytes(payload))
    assert batch.sleep_sessions == []
    assert batch.observations == []


def test_daily_sleep_missing_daily_sleep_dto_produces_empty_batch() -> None:
    batch = parse_daily_sleep_json(_bytes({"sleepLevels": []}))
    assert batch.sleep_sessions == []
    assert batch.observations == []


# --- parse_daily_hrv_json: garmin_connect's live get_hrv_data() shape (previously never
# fetched by this adapter at all -- see adapters/garmin_connect.py) --------------------------

DAILY_HRV: dict[str, object] = {
    "userProfilePk": 87061520,
    "hrvSummary": {
        "calendarDate": "2025-06-01",
        "weeklyAvg": 41,
        "lastNightAvg": 37,
        "lastNight5MinHigh": 61,
        "baseline": {"lowUpper": 39, "balancedLow": 42, "balancedUpper": 58},
        "status": "UNBALANCED",
        "feedbackPhrase": "HRV_UNBALANCED_8",
        "createTimeStamp": "2025-06-01T16:17:57.571",
    },
    "hrvReadings": [
        {
            "hrvValue": 32,
            "readingTimeGMT": "2025-06-01T08:38:11.0",
            "readingTimeLocal": "2025-06-01T01:38:11.0",
        }
    ],
}


def test_daily_hrv_scalar_fields_become_observations() -> None:
    batch = parse_daily_hrv_json(_bytes(DAILY_HRV))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["garmin.daily_hrv.lastNightAvg"].value_num == 37.0
    assert obs["garmin.daily_hrv.weeklyAvg"].value_num == 41.0
    assert obs["garmin.daily_hrv.lastNight5MinHigh"].value_num == 61.0
    assert obs["garmin.daily_hrv.status"].value_text == "UNBALANCED"


def test_daily_hrv_anchors_at_midnight_of_calendar_date() -> None:
    """createTimeStamp carries no explicit UTC/local marker in the real response, unlike the
    "...Gmt"-suffixed fields elsewhere -- anchored at midnight instead of guessing its
    timezone, same fallback parse_hydration_json already uses."""
    batch = parse_daily_hrv_json(_bytes(DAILY_HRV))
    obs = next(o for o in batch.observations if o.metric_key == "garmin.daily_hrv.lastNightAvg")
    assert obs.observed_at_utc == datetime(2025, 6, 1, 0, 0, 0)
    assert obs.local_date == "2025-06-01"
    assert obs.aggregation == "daily"


def test_daily_hrv_identifiers_and_nested_values_excluded() -> None:
    batch = parse_daily_hrv_json(_bytes(DAILY_HRV))
    keys = {o.metric_key for o in batch.observations}
    assert "garmin.daily_hrv.calendarDate" not in keys
    assert "garmin.daily_hrv.createTimeStamp" not in keys
    assert "garmin.daily_hrv.baseline" not in keys
    assert "garmin.daily_hrv.baseline" in batch.unrecognized_field_keys


def test_daily_hrv_does_not_touch_the_top_level_hrv_readings_array() -> None:
    """hrvReadings is a sibling of hrvSummary, not nested inside it -- confirming it never
    leaks into observations even though _flatten_scalars only ever sees hrvSummary."""
    batch = parse_daily_hrv_json(_bytes(DAILY_HRV))
    keys = {o.metric_key for o in batch.observations}
    assert not any("hrvReadings" in k for k in keys)


def test_daily_hrv_null_top_level_response_produces_empty_batch() -> None:
    """get_hrv_data() itself returns None (matching its own `dict | None` type) for a day HRV
    hasn't been computed for yet -- confirmed real API behavior, not a made-up edge case."""
    batch = parse_daily_hrv_json(json.dumps(None).encode("utf-8"))
    assert batch.observations == []


def test_daily_hrv_missing_hrv_summary_produces_empty_batch() -> None:
    batch = parse_daily_hrv_json(_bytes({"userProfilePk": 87061520}))
    assert batch.observations == []


# --- parse_daily_training_readiness_json: garmin_connect's live get_training_readiness() shape
# (previously never fetched by this adapter at all -- see adapters/garmin_connect.py) ---------

DAILY_TRAINING_READINESS: list[dict[str, object]] = [
    {
        "userProfilePK": 87061520,
        "deviceId": 3492916187,
        "calendarDate": "2025-06-01",
        "timestamp": "2025-06-01T08:38:11.0",
        "timestampLocal": "2025-06-01T01:38:11.0",
        "level": "MODERATE",
        "feedbackLong": "MODERATE_5",
        "feedbackShort": "GOOD_RECOVERY",
        "score": 62,
        "sleepScore": 82,
        "sleepScoreFactorPercent": 30,
        "recoveryTime": 240,
        "acwrFactorPercent": 15,
        "hrvWeeklyAverage": 41,
        "validSleep": True,
    },
    {
        "userProfilePK": 87061520,
        "calendarDate": "2025-06-01",
        "timestamp": "2025-06-01T00:10:00.0",
        "score": 55,
    },
]


def test_daily_training_readiness_uses_only_the_newest_reading() -> None:
    """The list is newest-first -- only data[0] (the latest recalculation) becomes
    observations, not every intraday reading."""
    batch = parse_daily_training_readiness_json(_list_bytes(DAILY_TRAINING_READINESS))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["garmin.daily_training_readiness.score"].value_num == 62.0


def test_daily_training_readiness_scalar_fields_become_observations() -> None:
    batch = parse_daily_training_readiness_json(_list_bytes(DAILY_TRAINING_READINESS))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["garmin.daily_training_readiness.sleepScore"].value_num == 82.0
    assert obs["garmin.daily_training_readiness.recoveryTime"].value_num == 240.0
    assert obs["garmin.daily_training_readiness.level"].value_text == "MODERATE"
    assert obs["garmin.daily_training_readiness.feedbackLong"].value_text == "MODERATE_5"


def test_daily_training_readiness_anchors_at_timestamp() -> None:
    batch = parse_daily_training_readiness_json(_list_bytes(DAILY_TRAINING_READINESS))
    obs = next(
        o for o in batch.observations if o.metric_key == "garmin.daily_training_readiness.score"
    )
    assert obs.observed_at_utc == datetime(2025, 6, 1, 8, 38, 11)
    assert obs.local_date == "2025-06-01"


def test_daily_training_readiness_identifiers_excluded() -> None:
    batch = parse_daily_training_readiness_json(_list_bytes(DAILY_TRAINING_READINESS))
    keys = {o.metric_key for o in batch.observations}
    assert "garmin.daily_training_readiness.deviceId" not in keys
    assert "garmin.daily_training_readiness.calendarDate" not in keys
    assert "garmin.daily_training_readiness.timestamp" not in keys
    assert "garmin.daily_training_readiness.timestampLocal" not in keys


def test_daily_training_readiness_booleans_are_skipped_entirely() -> None:
    batch = parse_daily_training_readiness_json(_list_bytes(DAILY_TRAINING_READINESS))
    keys = {o.metric_key for o in batch.observations}
    assert "garmin.daily_training_readiness.validSleep" not in keys
    assert "garmin.daily_training_readiness.validSleep" not in batch.unrecognized_field_keys


def test_daily_training_readiness_empty_list_produces_empty_batch() -> None:
    """Nothing computed yet for the day -- confirmed real API behavior, not a made-up edge
    case (matches the empty-list precedent already confirmed for get_hrv_data's None case)."""
    batch = parse_daily_training_readiness_json(_list_bytes([]))
    assert batch.observations == []


def test_daily_training_readiness_non_dict_entry_produces_empty_batch() -> None:
    batch = parse_daily_training_readiness_json(json.dumps([None]).encode("utf-8"))
    assert batch.observations == []


def test_daily_training_readiness_missing_calendar_date_produces_empty_batch() -> None:
    batch = parse_daily_training_readiness_json(_list_bytes([{"score": 55}]))
    assert batch.observations == []


# --- parse_daily_training_status_json: garmin_connect's live get_training_status() shape --
# one endpoint covering three GDPR-export report kinds' worth of data (see json_parser.py's own
# module comment above parse_daily_training_status_json) -------------------------------------

DAILY_TRAINING_STATUS: dict[str, object] = {
    "mostRecentVO2Max": {
        "generic": {
            "calendarDate": "2025-06-01",
            "vo2MaxPreciseValue": 48.3,
            "vo2MaxValue": 48.0,
            "fitnessAge": 29,
            "fitnessAgeDescription": "BETTER_THAN_AVERAGE",
            "maxMetCategory": 0,
        },
        "cycling": None,
        "heatAltitudeAcclimation": {
            "calendarDate": "2025-06-01",
            "altitudeAcclimation": 0,
            "heatAcclimationPercentage": 32,
            "heatTrend": "STEADY",
            "altitudeTrend": "STEADY",
            "currentAltitude": 50,
        },
    },
    "mostRecentTrainingLoadBalance": {"ignored": True},
    "mostRecentTrainingStatus": {
        "latestTrainingStatusData": {
            "3492916187": {
                "calendarDate": "2025-06-01",
                "sinceDate": "2025-05-01",
                "deviceId": 3492916187,
                "weeklyTrainingLoad": 420,
                "trainingStatus": 6,
                "fitnessTrend": 1,
                "trainingStatusFeedbackPhrase": "PRODUCTIVE_1",
                "trainingPaused": False,
            }
        }
    },
}


def test_daily_training_status_vo2max_section_becomes_observations() -> None:
    batch = parse_daily_training_status_json(_bytes(DAILY_TRAINING_STATUS))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["garmin.daily_vo2max.vo2MaxValue"].value_num == 48.0
    assert obs["garmin.daily_vo2max.vo2MaxPreciseValue"].value_num == 48.3
    assert obs["garmin.daily_vo2max.fitnessAge"].value_num == 29.0
    assert "garmin.daily_vo2max.calendarDate" not in obs
    assert "garmin.daily_vo2max.fitnessAgeDescription" not in obs


def test_daily_training_status_heat_altitude_section_becomes_observations() -> None:
    batch = parse_daily_training_status_json(_bytes(DAILY_TRAINING_STATUS))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["garmin.daily_heat_altitude.heatAcclimationPercentage"].value_num == 32.0
    assert obs["garmin.daily_heat_altitude.heatTrend"].value_text == "STEADY"
    assert "garmin.daily_heat_altitude.calendarDate" not in obs


def test_daily_training_status_training_status_section_uses_first_device() -> None:
    """Keyed by deviceId in the real response -- this athlete has always had exactly one
    recording device, so the first (only) value is taken."""
    batch = parse_daily_training_status_json(_bytes(DAILY_TRAINING_STATUS))
    obs = {o.metric_key: o for o in batch.observations}
    assert obs["garmin.daily_training_status.weeklyTrainingLoad"].value_num == 420.0
    assert obs["garmin.daily_training_status.trainingStatus"].value_num == 6.0
    assert obs["garmin.daily_training_status.trainingStatusFeedbackPhrase"].value_text == (
        "PRODUCTIVE_1"
    )
    assert "garmin.daily_training_status.deviceId" not in obs
    assert "garmin.daily_training_status.sinceDate" not in obs


def test_daily_training_status_sections_anchor_at_midnight_of_calendar_date() -> None:
    batch = parse_daily_training_status_json(_bytes(DAILY_TRAINING_STATUS))
    obs = next(o for o in batch.observations if o.metric_key == "garmin.daily_vo2max.vo2MaxValue")
    assert obs.observed_at_utc == datetime(2025, 6, 1, 0, 0, 0)
    assert obs.local_date == "2025-06-01"


def test_daily_training_status_missing_section_is_silently_skipped() -> None:
    """A device/sport with nothing computed for it that day is simply absent, not a
    fabricated zero -- e.g. cycling VO2max is null in the fixture above."""
    batch = parse_daily_training_status_json(_bytes(DAILY_TRAINING_STATUS))
    keys = {o.metric_key for o in batch.observations}
    assert not any("cycling" in k for k in keys)


def test_daily_training_status_all_sections_missing_produces_empty_batch() -> None:
    batch = parse_daily_training_status_json(_bytes({}))
    assert batch.observations == []


def test_daily_training_status_non_dict_top_level_produces_empty_batch() -> None:
    batch = parse_daily_training_status_json(json.dumps(None).encode("utf-8"))
    assert batch.observations == []


# --- parse_daily_race_predictions_json: garmin_connect's live get_race_predictions() shape --
# a range call, one record per day in the requested window, unlike every other live parser in
# this module (see json_parser.py's own module comment) ---------------------------------------

DAILY_RACE_PREDICTIONS: list[dict[str, object]] = [
    {
        "userId": 87061520,
        "fromCalendarDate": "2025-06-01",
        "toCalendarDate": "2025-06-02",
        "calendarDate": "2025-06-01",
        "time5K": 1320,
        "time10K": 2760,
        "timeHalfMarathon": 6120,
        "timeMarathon": 12900,
    },
    {
        "userId": 87061520,
        "fromCalendarDate": "2025-06-01",
        "toCalendarDate": "2025-06-02",
        "calendarDate": "2025-06-02",
        "time5K": 1315,
        "time10K": 2750,
        "timeHalfMarathon": 6100,
        "timeMarathon": 12850,
    },
]


def test_daily_race_predictions_each_day_becomes_its_own_observations() -> None:
    batch = parse_daily_race_predictions_json(_list_bytes(DAILY_RACE_PREDICTIONS))
    values = {
        (o.metric_key, o.local_date): o.value_num
        for o in batch.observations
        if o.metric_key == "garmin.daily_race_predictions.time5K"
    }
    assert values[("garmin.daily_race_predictions.time5K", "2025-06-01")] == 1320.0
    assert values[("garmin.daily_race_predictions.time5K", "2025-06-02")] == 1315.0


def test_daily_race_predictions_anchors_at_midnight_of_calendar_date() -> None:
    batch = parse_daily_race_predictions_json(_list_bytes(DAILY_RACE_PREDICTIONS))
    obs = next(
        o
        for o in batch.observations
        if o.metric_key == "garmin.daily_race_predictions.time5K" and o.local_date == "2025-06-01"
    )
    assert obs.observed_at_utc == datetime(2025, 6, 1, 0, 0, 0)


def test_daily_race_predictions_identifiers_excluded() -> None:
    batch = parse_daily_race_predictions_json(_list_bytes(DAILY_RACE_PREDICTIONS))
    keys = {o.metric_key for o in batch.observations}
    assert "garmin.daily_race_predictions.userId" not in keys
    assert "garmin.daily_race_predictions.fromCalendarDate" not in keys
    assert "garmin.daily_race_predictions.toCalendarDate" not in keys
    assert "garmin.daily_race_predictions.calendarDate" not in keys


def test_daily_race_predictions_record_missing_calendar_date_is_skipped_not_fatal() -> None:
    extra_record: dict[str, object] = {"userId": 87061520, "time5K": 9999}
    records: list[dict[str, object]] = [*DAILY_RACE_PREDICTIONS, extra_record]
    batch = parse_daily_race_predictions_json(_list_bytes(records))
    values = {
        o.value_num
        for o in batch.observations
        if o.metric_key == "garmin.daily_race_predictions.time5K"
    }
    assert values == {1320.0, 1315.0}


def test_daily_race_predictions_non_list_top_level_produces_empty_batch() -> None:
    batch = parse_daily_race_predictions_json(_bytes({"time5K": 1320}))
    assert batch.observations == []
