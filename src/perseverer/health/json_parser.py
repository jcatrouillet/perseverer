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
from datetime import UTC, datetime
from typing import Any

from perseverer.health.types import (
    HealthBatch,
    HealthObservation,
    ParsedSleepSession,
    ParsedSleepStage,
)

# Identifiers, not health facts — excluded from becoming observations.
_SKIP_FIELDS = frozenset({"userProfileId", "userId", "userDailySummaryId", "uuid"})


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    return float(value) if isinstance(value, int | float) else None


def _parse_garmin_datetime(value: str) -> datetime:
    """Garmin's own JSON timestamps look like "2025-01-02T08:00:00.0" — naive per this
    project's naive-implicitly-UTC convention (db/schema.py); the "Gmt"-suffixed fields these
    come from are already UTC instants, so no conversion is needed, just cleanup.
    """
    cleaned = value.replace("Z", "")
    if "." in cleaned:
        cleaned = cleaned.split(".")[0]
    return datetime.fromisoformat(cleaned)


def _flatten_scalars(
    data: dict[str, Any],
    *,
    key_prefix: str,
    exclude_keys: frozenset[str],
    anchor: datetime,
    local_date: str,
) -> tuple[list[HealthObservation], list[str]]:
    """Shared per-record flattening: scalar fields become observations, nested dict/list
    values are cataloged (never flattened) rather than dropped, booleans/None are skipped
    entirely (nothing meaningful to store). See module docstring.
    """
    observations = []
    unrecognized = []
    for key, value in data.items():
        if key in exclude_keys:
            continue
        if value is None or isinstance(value, bool):
            continue
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
    return observations, unrecognized


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

    observations, unrecognized = _flatten_scalars(
        data,
        key_prefix=key_prefix,
        exclude_keys=_SKIP_FIELDS | {date_field},
        anchor=anchor,
        local_date=local_date,
    )
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


# Garmin Connect's live daily-sleep endpoint (`Garmin.get_sleep_data`) never appeared in the
# garmin_connect adapter's own daily sync at all -- only fit_folder/garmin_export ever produced
# sleep_session rows, from monitoring FIT files bundled in an export archive, which is why real
# sleep data silently stopped the moment the last export backfill's data ran out even though the
# daily incremental sync kept running. Shape confirmed by a live call against this athlete's own
# real account (not assumed from docs, per this project's own verification discipline): a
# `dailySleepDTO` object (totals, epoch-ms start/end, a nested `sleepScores.overall.value`)
# alongside a sibling top-level `sleepLevels` array of stage segments. `activityLevel`'s
# 0/1/2/3 -> deep/light/rem/awake mapping was confirmed by summing each value's own segment
# durations and checking they exactly equal dailySleepDTO's own deep/light/rem/awakeSleepSeconds
# totals for the same day.
_SLEEP_STAGE_BY_ACTIVITY_LEVEL = {0.0: "deep", 1.0: "light", 2.0: "rem", 3.0: "awake"}

# Raw identifiers and the epoch-ms timestamps already consumed into the session's own
# start/end fields -- everything else scalar in dailySleepDTO becomes a garmin.daily_sleep.*
# observation (average respiration/SpO2/heart rate/stress, awake count, sleep-need baseline,
# etc.); nested objects (sleepScores, sleepNeed, nextSleepNeed) are cataloged, not flattened,
# same as every other nested value _flatten_scalars sees.
_DAILY_SLEEP_SKIP_FIELDS = _SKIP_FIELDS | {
    "id",
    "deviceId",
    "userProfilePK",
    "calendarDate",
    "sleepStartTimestampGMT",
    "sleepEndTimestampGMT",
    "sleepStartTimestampLocal",
    "sleepEndTimestampLocal",
    "autoSleepStartTimestampGMT",
    "autoSleepEndTimestampGMT",
}


def _epoch_ms_to_utc(value: Any) -> datetime | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).replace(tzinfo=None)


def _daily_sleep_stages(sleep_levels: Any) -> list[ParsedSleepStage]:
    if not isinstance(sleep_levels, list):
        return []
    stages = []
    for row in sleep_levels:
        if not isinstance(row, dict):
            continue
        activity_level = row.get("activityLevel")
        stage_name = (
            _SLEEP_STAGE_BY_ACTIVITY_LEVEL.get(activity_level)
            if isinstance(activity_level, int | float)
            else None
        )
        start_raw, end_raw = row.get("startGMT"), row.get("endGMT")
        if stage_name is None or not isinstance(start_raw, str) or not isinstance(end_raw, str):
            continue
        start = _parse_garmin_datetime(start_raw)
        end = _parse_garmin_datetime(end_raw)
        if end > start:
            stages.append(ParsedSleepStage(stage_name, start, end))
    return stages


def parse_daily_sleep_json(raw_bytes: bytes) -> HealthBatch:
    """One day's response from `Garmin.get_sleep_data(cdate)`. A day with nothing tracked yet
    still returns a `dailySleepDTO`, just with null start/end timestamps -- that produces an
    empty batch (no session, no observations), not a zero-length one.
    """
    data: dict[str, Any] = json.loads(raw_bytes)
    daily = data.get("dailySleepDTO")
    if not isinstance(daily, dict):
        return HealthBatch()

    local_date = daily.get("calendarDate")
    start = _epoch_ms_to_utc(daily.get("sleepStartTimestampGMT"))
    end = _epoch_ms_to_utc(daily.get("sleepEndTimestampGMT"))
    if not isinstance(local_date, str) or start is None or end is None:
        return HealthBatch()

    overall_score = None
    scores = daily.get("sleepScores")
    if isinstance(scores, dict) and isinstance(scores.get("overall"), dict):
        overall_score = _to_float(scores["overall"].get("value"))

    session = ParsedSleepSession(
        local_date=local_date,
        start_time_utc=start,
        end_time_utc=end,
        total_sleep_s=_to_float(daily.get("sleepTimeSeconds")) or (end - start).total_seconds(),
        sleep_score=overall_score,
        stages=_daily_sleep_stages(data.get("sleepLevels")),
    )

    observations, unrecognized = _flatten_scalars(
        daily,
        key_prefix="garmin.daily_sleep",
        exclude_keys=_DAILY_SLEEP_SKIP_FIELDS,
        anchor=end,
        local_date=local_date,
    )
    return HealthBatch(
        observations=observations,
        sleep_sessions=[session],
        unrecognized_field_keys=sorted(set(unrecognized)),
    )


# Garmin Connect's live daily-HRV endpoint (`Garmin.get_hrv_data`) -- same story as
# parse_daily_sleep_json above: garmin_connect.py never fetched HRV at all, only
# fit_folder/garmin_export historical backfills ever produced hrv.last_night_average /
# garmin.export.TrainingReadinessDTO.hrvWeeklyAverage observations, so real HRV silently
# stopped the moment the last export backfill's own data ran out even though the daily
# incremental sync kept running. Shape confirmed by a live call against this athlete's own real
# account: a top-level `hrvSummary` object (weeklyAvg, lastNightAvg, lastNight5MinHigh, a
# nested `baseline`, status, feedbackPhrase) plus a `hrvReadings` array of ~5-minute raw
# readings -- the array isn't ingested here, this project has no HRV health_stream consumer
# yet and the summary fields are what's actually missing from the dashboard.
_DAILY_HRV_SKIP_FIELDS = _SKIP_FIELDS | {"calendarDate", "createTimeStamp"}


def parse_daily_hrv_json(raw_bytes: bytes) -> HealthBatch:
    """One day's response from `Garmin.get_hrv_data(cdate)` -- the whole top-level value is
    `null` for a day HRV hasn't been computed for yet (confirmed real API behavior, matching the
    method's own `dict[str, Any] | None` return type), which produces an empty batch here, not
    a fabricated one.
    """
    data: Any = json.loads(raw_bytes)
    if not isinstance(data, dict):
        return HealthBatch()
    summary = data.get("hrvSummary")
    if not isinstance(summary, dict):
        return HealthBatch()

    local_date = summary.get("calendarDate")
    if not isinstance(local_date, str):
        return HealthBatch()

    # createTimeStamp carries no explicit UTC/local marker in the real response (unlike the
    # "...Gmt"-suffixed fields elsewhere in this module) -- anchoring at midnight of local_date
    # rather than guessing its timezone, same fallback parse_hydration_json already uses.
    anchor = datetime.fromisoformat(local_date)
    observations, unrecognized = _flatten_scalars(
        summary,
        key_prefix="garmin.daily_hrv",
        exclude_keys=_DAILY_HRV_SKIP_FIELDS,
        anchor=anchor,
        local_date=local_date,
    )
    return HealthBatch(observations=observations, unrecognized_field_keys=sorted(set(unrecognized)))


# Garmin Connect's live daily-training-readiness endpoint (`Garmin.get_training_readiness`) --
# same never-fetched-live story as sleep/HRV above, this time for
# garmin.export.TrainingReadinessDTO.* (the readiness score/level/feedback fields specifically;
# hrvWeeklyAverage from this same export report kind is already covered by
# parse_daily_hrv_json). Shape confirmed by a live call: a JSON *array* of readings for the day
# (one per update -- after-wakeup, after-exercise, real-time recalculation, ...), newest first.
# Only the newest reading is kept, matching "today's current readiness" being the one thing a
# dashboard would ever want, not a log of every intraday recalculation.
_DAILY_TRAINING_READINESS_SKIP_FIELDS = _SKIP_FIELDS | {
    "deviceId",
    "calendarDate",
    "timestamp",
    "timestampLocal",
}


def parse_daily_training_readiness_json(raw_bytes: bytes) -> HealthBatch:
    """One day's response from `Garmin.get_training_readiness(cdate)` -- a list, newest reading
    first; an empty list (nothing computed yet) produces an empty batch."""
    data: Any = json.loads(raw_bytes)
    if not isinstance(data, list) or len(data) == 0:
        return HealthBatch()
    latest = data[0]
    if not isinstance(latest, dict):
        return HealthBatch()

    local_date = latest.get("calendarDate")
    if not isinstance(local_date, str):
        return HealthBatch()

    timestamp = latest.get("timestamp")
    anchor = (
        _parse_garmin_datetime(timestamp)
        if isinstance(timestamp, str)
        else datetime.fromisoformat(local_date)
    )
    observations, unrecognized = _flatten_scalars(
        latest,
        key_prefix="garmin.daily_training_readiness",
        exclude_keys=_DAILY_TRAINING_READINESS_SKIP_FIELDS,
        anchor=anchor,
        local_date=local_date,
    )
    return HealthBatch(observations=observations, unrecognized_field_keys=sorted(set(unrecognized)))


# Garmin Connect's live daily-training-status endpoint (`Garmin.get_training_status`) -- one
# call whose response nests exactly the data behind *three* separate GDPR-export report kinds
# (confirmed by comparing real field names): `mostRecentVO2Max.generic` (MetricsMaxMetData's own
# vo2MaxValue/maxMetCategory fields), `mostRecentVO2Max.heatAltitudeAcclimation`
# (MetricsHeatAltitudeAcclimation's fields, near-identical names), and
# `mostRecentTrainingStatus.latestTrainingStatusData` (TrainingHistory's trainingStatus/sport/
# fitnessTrend fields, keyed by deviceId -- this athlete has always had exactly one recording
# device, so the first value is taken rather than trying to merge multiple devices' status).
# None of these three were ever fetched live before, same "went stale the moment the last
# export backfill ran out" story as sleep/HRV/readiness above.
_DAILY_VO2MAX_SKIP_FIELDS = _SKIP_FIELDS | {"calendarDate", "fitnessAgeDescription"}
_DAILY_HEAT_ALTITUDE_SKIP_FIELDS = _SKIP_FIELDS | {"calendarDate"}
_DAILY_TRAINING_STATUS_SKIP_FIELDS = _SKIP_FIELDS | {"deviceId", "calendarDate", "sinceDate"}


def _flatten_training_status_section(
    section: Any, *, key_prefix: str, exclude_keys: frozenset[str], local_date: str
) -> tuple[list[HealthObservation], list[str]]:
    if not isinstance(section, dict):
        return [], []
    anchor = datetime.fromisoformat(local_date)  # no per-section GMT timestamp in this response
    return _flatten_scalars(
        section,
        key_prefix=key_prefix,
        exclude_keys=exclude_keys,
        anchor=anchor,
        local_date=local_date,
    )


def parse_daily_training_status_json(raw_bytes: bytes) -> HealthBatch:
    """One day's response from `Garmin.get_training_status(cdate)`. Each of the three sections
    (VO2max, heat/altitude acclimation, training status) is independently optional -- a
    device/sport with nothing computed for it that day is simply absent from the batch, not a
    fabricated zero."""
    data: Any = json.loads(raw_bytes)
    if not isinstance(data, dict):
        return HealthBatch()

    observations: list[HealthObservation] = []
    unrecognized: list[str] = []

    vo2max_root = data.get("mostRecentVO2Max")
    vo2max_generic = vo2max_root.get("generic") if isinstance(vo2max_root, dict) else None
    if isinstance(vo2max_generic, dict) and isinstance(vo2max_generic.get("calendarDate"), str):
        obs, unrec = _flatten_training_status_section(
            vo2max_generic,
            key_prefix="garmin.daily_vo2max",
            exclude_keys=_DAILY_VO2MAX_SKIP_FIELDS,
            local_date=vo2max_generic["calendarDate"],
        )
        observations.extend(obs)
        unrecognized.extend(unrec)

    heat_altitude = (
        vo2max_root.get("heatAltitudeAcclimation") if isinstance(vo2max_root, dict) else None
    )
    if isinstance(heat_altitude, dict) and isinstance(heat_altitude.get("calendarDate"), str):
        obs, unrec = _flatten_training_status_section(
            heat_altitude,
            key_prefix="garmin.daily_heat_altitude",
            exclude_keys=_DAILY_HEAT_ALTITUDE_SKIP_FIELDS,
            local_date=heat_altitude["calendarDate"],
        )
        observations.extend(obs)
        unrecognized.extend(unrec)

    status_root = data.get("mostRecentTrainingStatus")
    status_by_device = (
        status_root.get("latestTrainingStatusData") if isinstance(status_root, dict) else None
    )
    status_latest = (
        next(iter(status_by_device.values()), None) if isinstance(status_by_device, dict) else None
    )
    if isinstance(status_latest, dict) and isinstance(status_latest.get("calendarDate"), str):
        obs, unrec = _flatten_training_status_section(
            status_latest,
            key_prefix="garmin.daily_training_status",
            exclude_keys=_DAILY_TRAINING_STATUS_SKIP_FIELDS,
            local_date=status_latest["calendarDate"],
        )
        observations.extend(obs)
        unrecognized.extend(unrec)

    return HealthBatch(observations=observations, unrecognized_field_keys=sorted(set(unrecognized)))


# Garmin Connect's live race-predictions endpoint (`Garmin.get_race_predictions`) -- unlike
# every other live fetch in this module, this one is a *range* call (one request covers the
# whole rolling window, not one request per day -- see adapters/garmin_connect.py), returning a
# list with one record per day. Field names differ from the GDPR export's own
# `garmin.export.RunRacePredictions` (`time5K` vs `raceTime5K`, etc.) because they're genuinely
# different Garmin API responses, same "different endpoint, same reading" story as sleep
# respiration elsewhere in this project -- not aliased to the same metric_key, kept as its own
# `garmin.daily_race_predictions` namespace.
_DAILY_RACE_PREDICTIONS_SKIP_FIELDS = _SKIP_FIELDS | {
    "fromCalendarDate",
    "toCalendarDate",
    "calendarDate",
}


def parse_daily_race_predictions_json(raw_bytes: bytes) -> HealthBatch:
    """The whole-range response from `Garmin.get_race_predictions(startdate, enddate,
    _type="daily")` -- one record per day in the range, each becoming its own set of
    observations (unlike the single-day parsers elsewhere in this module)."""
    data: Any = json.loads(raw_bytes)
    if not isinstance(data, list):
        return HealthBatch()

    observations: list[HealthObservation] = []
    unrecognized: list[str] = []
    for record in data:
        if not isinstance(record, dict):
            continue
        local_date = record.get("calendarDate")
        if not isinstance(local_date, str):
            continue
        anchor = datetime.fromisoformat(local_date)  # no per-day timestamp in this response
        obs, unrec = _flatten_scalars(
            record,
            key_prefix="garmin.daily_race_predictions",
            exclude_keys=_DAILY_RACE_PREDICTIONS_SKIP_FIELDS,
            anchor=anchor,
            local_date=local_date,
        )
        observations.extend(obs)
        unrecognized.extend(unrec)

    return HealthBatch(observations=observations, unrecognized_field_keys=sorted(set(unrecognized)))


# GDPR export health JSON (DI-Connect-Wellness/Metrics/Aggregator) — see
# docs/adr/0005-phase-2-garmin-export-real-data.md decisions 3-4. Identifiers, not health
# facts, on top of the ones _parse_generic already excludes.
_EXPORT_SKIP_FIELDS = _SKIP_FIELDS | {"userProfilePK", "deviceId"}

# Different report kinds name their most-precise timestamp differently; tried in this order
# rather than a per-report-kind config (ADR 0005 decision 4).
_EXPORT_ANCHOR_FIELDS = (
    "timestampGmt",
    "wellnessEndTimeGmt",
    "sleepEndTimestampGMT",
    "persistedTimestampGMT",
    "timestamp",
)


def parse_garmin_export_json(raw_bytes: bytes, *, report_kind: str) -> HealthBatch:
    """Parses one GDPR-export health JSON file: a JSON array of ~100 day/event records
    spanning a date range (defensively also accepts a single bare record/dict). Every
    recognized report kind (sleepData, UDSFile, HydrationLogFile, TrainingReadinessDTO, ...)
    is flattened the same generic way, namespaced by `report_kind` in the metric key --
    `garmin.export.<report_kind>.<field>` -- rather than one bespoke parser per kind.

    A record missing `calendarDate` is skipped, not fatal for the rest of the file: some
    report kinds may have occasional incomplete records among many good ones.
    """
    parsed: Any = json.loads(raw_bytes)
    records: list[dict[str, Any]] = parsed if isinstance(parsed, list) else [parsed]

    key_prefix = f"garmin.export.{report_kind}"
    observations: list[HealthObservation] = []
    unrecognized: list[str] = []

    for record in records:
        if not isinstance(record, dict):
            continue
        local_date = record.get("calendarDate")
        if not isinstance(local_date, str):
            continue

        anchor = None
        for field in _EXPORT_ANCHOR_FIELDS:
            value = record.get(field)
            if isinstance(value, str):
                anchor = _parse_garmin_datetime(value)
                break
        if anchor is None:
            anchor = datetime.fromisoformat(local_date)  # midnight — best available fallback

        record_obs, record_unrecognized = _flatten_scalars(
            record,
            key_prefix=key_prefix,
            exclude_keys=_EXPORT_SKIP_FIELDS | {"calendarDate"},
            anchor=anchor,
            local_date=local_date,
        )
        observations.extend(record_obs)
        unrecognized.extend(record_unrecognized)

    return HealthBatch(
        observations=observations, unrecognized_field_keys=sorted(set(unrecognized))
    )
