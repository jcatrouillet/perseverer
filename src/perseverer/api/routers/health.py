"""GET /health/observations, GET /health/dashboard, GET /health/stream."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

import pyarrow.parquet as pq
from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, func, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.common import Page, to_utc
from perseverer.api.schemas.health import (
    HealthDashboardDayOut,
    HealthDashboardMetricOut,
    HealthDashboardOut,
    HealthObservationOut,
    HealthStreamResponse,
)
from perseverer.config import Settings, get_settings
from perseverer.db.schema import health_metric_daily_rollup, health_observation, health_stream

router = APIRouter()

# One logical field can live under up to three raw metric_key namespaces depending on source
# and era (canonical FIT-derived, Connect-shaped JSON via fit_folder, GDPR export JSON) --
# confirmed against the real ingested database, not assumed from parser docstrings. Order
# matters: the first alias with data for a given day wins (see _merge_logical_metric). Deliberate
# false positives excluded (e.g. garmin.daily_summary.lastSevenDaysAvgRestingHeartRate is a
# distinct trailing-average metric, not an alias of the same daily value). See
# docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
LOGICAL_METRICS: dict[str, list[str]] = {
    "steps": ["garmin.daily_summary.totalSteps", "garmin.export.UDSFile.totalSteps"],
    "calories": [
        "garmin.daily_summary.totalKilocalories",
        "garmin.export.UDSFile.totalKilocalories",
    ],
    "resting_heart_rate": [
        "resting_heart_rate",
        "garmin.daily_summary.restingHeartRate",
        "garmin.export.UDSFile.restingHeartRate",
    ],
    "max_heart_rate": ["garmin.daily_summary.maxHeartRate", "garmin.export.UDSFile.maxHeartRate"],
    "floors_ascended": [
        "garmin.daily_summary.floorsAscendedInMeters",
        "garmin.export.UDSFile.floorsAscendedInMeters",
    ],
    "vo2max": [
        "garmin.export.MetricsMaxMetData.vo2MaxValue",
        "fit.max_met_data.vo2_max",
        "garmin.export.ActivityVo2Max.vo2MaxValue",
        # garmin_connect's live get_training_status() equivalent (see
        # health/json_parser.py::parse_daily_training_status_json) -- without this alias,
        # VO2max would have gone stale the same way sleep/HRV did, since none of the three
        # aliases above were ever fetched by the daily incremental sync.
        "garmin.daily_vo2max.vo2MaxValue",
    ],
    "hrv_nightly_average": [
        "hrv.last_night_average",
        "garmin.export.TrainingReadinessDTO.hrvWeeklyAverage",
        "garmin.daily_hrv.lastNightAvg",
    ],
    "spo2_average": [
        "garmin.daily_summary.averageSpo2",
        "garmin.export.UDSFile.averageSpo2Value",
    ],
    "stress_average": [
        "garmin.daily_summary.averageStressLevel",
        "garmin.export.UDSFile.allDayStress",
    ],
    # Two genuinely distinct readings, not aliases of each other -- confirmed against the real
    # archive: waking respiration (a daily-summary figure, 103 real observations, coverage ends
    # 2025-04-17) and sleep respiration (from the night's sleep session, 1365 real observations,
    # current through the latest ingested night). Kept as separate logical metrics rather than
    # merged, since a caller (the week view's wellness charts) wants to show both at once.
    "waking_respiration_rate": ["garmin.daily_summary.avgWakingRespirationValue"],
    # garmin.export.sleepData is the historical GDPR-export shape (garmin_export backfill only);
    # garmin.daily_sleep is the live equivalent (garmin_connect's own daily sync, see
    # health/json_parser.py::parse_daily_sleep_json) -- same reading, different field name
    # (`averageRespiration` vs `averageRespirationValue`) because the two are genuinely
    # different Garmin API responses, not a naming inconsistency to paper over.
    "sleep_respiration_rate": [
        "garmin.export.sleepData.averageRespiration",
        "garmin.daily_sleep.averageRespirationValue",
    ],
    # Body composition -- Eufy smart scale is the primary source (see adapters/eufy.py); weight/
    # bmi/body_fat_pct additionally carry a second, older alias from adapters/apple_health_export.py
    # (Apple Health export data predating the Eufy scale, cutoff at Eufy's own earliest reading --
    # see that adapter's docstring) since the two never share a date by construction. The curated,
    # human-meaningful subset of the ~24 raw eufy.scale.* fields that get promoted to the
    # dashboard; the rest are still fully stored/queryable via GET /health/observations, matching
    # the same "catalog broadly, surface a curated subset" split already used for Garmin's own
    # larger raw field set.
    "weight_kg": ["eufy.scale.weight", "apple_health.body_mass"],
    "bmi": ["eufy.scale.bmi", "apple_health.body_mass_index"],
    "body_fat_pct": ["eufy.scale.body_fat", "apple_health.body_fat_percentage"],
    "muscle_mass_kg": ["eufy.scale.muscle_mass"],
    "bone_mass_kg": ["eufy.scale.bone_mass"],
    "water_pct": ["eufy.scale.water"],
    "bmr_kcal": ["eufy.scale.bmr"],
    "visceral_fat": ["eufy.scale.visceral_fat"],
    "metabolic_age": ["eufy.scale.body_age"],
    "protein_ratio_pct": ["eufy.scale.protein_ratio"],
}


@router.get("/health/observations")
def list_health_observations(
    athlete_id: Annotated[str, Depends(require_api_key)],
    metric_key: list[str] = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    conn: Connection = Depends(get_conn),
) -> Page[HealthObservationOut]:
    query = select(health_observation).where(
        health_observation.c.athlete_id == athlete_id,
        health_observation.c.metric_key.in_(metric_key),
        health_observation.c.local_date >= start_date.isoformat(),
        health_observation.c.local_date <= end_date.isoformat(),
    )
    total = conn.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = conn.execute(
        query.order_by(health_observation.c.observed_at_utc).limit(limit).offset(offset)
    ).fetchall()

    items = [
        HealthObservationOut(
            metric_key=r.metric_key,
            observed_at_utc=to_utc(r.observed_at_utc),
            local_date=r.local_date,
            aggregation=r.aggregation,
            value_num=r.value_num,
            value_text=r.value_text,
            unit=r.unit,
            source=r.source,
        )
        for r in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


# Body composition logical metrics come from a Eufy smart scale that's sometimes shared with
# other people -- a reading from a different, much lighter/heavier person lands as a real,
# correctly-parsed value with no way to tell it apart from the athlete's own at ingest time
# (same device, same user_id in Eufy's own raw JSON). Confirmed against real anomalies of
# increasing severity: an isolated 51.9kg reading amid ~79kg days; a day (2025-09-21) with
# *three* raw readings, one real (~81.3kg) and two duplicates from someone else (~48.1kg), whose
# naive daily average would already be contaminated before any day-level check ran; and a whole
# multi-week stretch (2025-09 through 2025-11) where the other person's ~45-48kg readings actually
# outnumber the athlete's own ~80kg ones locally -- which defeats any filter that compares a
# reading to its nearby peers (a wider window doesn't help: the "wrong" cluster is the local
# majority there, not a minority). Filtered here, at the display layer, using a *sequential*
# baseline instead (see _body_composition_daily): the raw archive and health_observation rows
# stay untouched (raw-first), only what the dashboard/charts render is affected. Each of these
# logical metrics has at most two aliases (Eufy, plus Apple Health for weight/bmi/body_fat_pct's
# pre-Eufy era -- the two never share a date by construction, see
# adapters/apple_health_export.py), so filtering reads straight from health_observation instead
# of health_metric_daily_rollup. See docs/DATA_DICTIONARY.md's Eufy section.
_OUTLIER_FILTERED_METRICS = frozenset(
    {
        "weight_kg",
        "bmi",
        "body_fat_pct",
        "muscle_mass_kg",
        "bone_mass_kg",
        "water_pct",
        "bmr_kcal",
        "visceral_fat",
        "metabolic_age",
        "protein_ratio_pct",
    }
)


def _body_composition_daily(
    conn: Connection,
    *,
    athlete_id: str,
    aliases: list[str],
    start_date: date,
    end_date: date,
    max_relative_deviation: float = 0.15,
) -> HealthDashboardMetricOut | None:
    """Rebuilds the daily aggregate for one body-composition metric directly from raw
    `health_observation` rows, walking the *entire* history (across every alias in `aliases`,
    e.g. Eufy plus Apple Health's pre-Eufy era -- the two never share a date by construction) in
    chronological order and rejecting a reading if it deviates more than `max_relative_deviation`
    from the last *accepted* reading -- not from its nearby peers. A symmetric neighbor-window
    comparison (tried first, see git history) breaks down on real data: a shared scale can go
    through a multi-week stretch where someone else's readings actually outnumber the athlete's
    own within any reasonably-sized window, so a plain local median gets pulled toward the wrong
    cluster. Anchoring to the last accepted value instead means a whole run of bad readings gets
    rejected together, however many of them there are or how tightly they cluster near each
    other, since they're never compared to each other -- only to the last value that was itself
    accepted. The very first-ever reading for a metric has nothing to compare against and is
    always accepted.
    """
    rows = conn.execute(
        select(
            health_observation.c.local_date,
            health_observation.c.observed_at_utc,
            health_observation.c.value_num,
            health_observation.c.metric_key,
        )
        .where(
            health_observation.c.athlete_id == athlete_id,
            health_observation.c.metric_key.in_(aliases),
            health_observation.c.value_num.is_not(None),
        )
        .order_by(health_observation.c.observed_at_utc)
    ).fetchall()

    baseline: float | None = None
    by_date: dict[str, list[float]] = {}
    last_by_date: dict[str, tuple[object, float]] = {}
    source_by_date: dict[str, str] = {}
    last_accepted_date: str | None = None
    for row in rows:
        value = row.value_num
        accepted = (
            baseline is None
            or baseline == 0
            or abs(value - baseline) / abs(baseline) <= max_relative_deviation
        )
        if not accepted:
            continue
        baseline = value
        last_accepted_date = row.local_date

        row_date = date.fromisoformat(row.local_date)
        if not (start_date <= row_date <= end_date):
            continue
        by_date.setdefault(row.local_date, []).append(value)
        source_by_date[row.local_date] = row.metric_key
        current_last = last_by_date.get(row.local_date)
        if current_last is None or row.observed_at_utc > current_last[0]:
            last_by_date[row.local_date] = (row.observed_at_utc, value)

    daily = [
        HealthDashboardDayOut(
            local_date=local_date,
            value_sum=sum(values),
            value_avg=sum(values) / len(values),
            value_min=min(values),
            value_max=max(values),
            value_last=last_by_date[local_date][1],
            n_observations=len(values),
            source_metric_key=source_by_date[local_date],
        )
        for local_date, values in sorted(by_date.items())
    ]

    if not daily and last_accepted_date is None:
        return None

    return HealthDashboardMetricOut(
        logical_metric="", last_observed=last_accepted_date, daily=daily
    )


def _merge_logical_metric(
    conn: Connection,
    *,
    athlete_id: str,
    aliases: list[str],
    start_date: date,
    end_date: date,
) -> HealthDashboardMetricOut | None:
    rows = conn.execute(
        select(health_metric_daily_rollup).where(
            health_metric_daily_rollup.c.athlete_id == athlete_id,
            health_metric_daily_rollup.c.metric_key.in_(aliases),
            health_metric_daily_rollup.c.local_date >= start_date.isoformat(),
            health_metric_daily_rollup.c.local_date <= end_date.isoformat(),
        )
    ).fetchall()

    # First alias (in priority order) with a row for that date wins -- a namespace switchover
    # (e.g. export-backfill era vs. live-sync era) never produces two rows for the same day.
    by_date: dict[str, HealthDashboardDayOut] = {}
    for r in rows:
        existing = by_date.get(r.local_date)
        if existing is not None and aliases.index(existing.source_metric_key) <= aliases.index(
            r.metric_key
        ):
            continue
        by_date[r.local_date] = HealthDashboardDayOut(
            local_date=r.local_date,
            value_sum=r.value_sum,
            value_avg=r.value_avg,
            value_min=r.value_min,
            value_max=r.value_max,
            value_last=r.value_last,
            n_observations=r.n_observations,
            source_metric_key=r.metric_key,
        )

    last_observed = conn.execute(
        select(func.max(health_metric_daily_rollup.c.local_date)).where(
            health_metric_daily_rollup.c.athlete_id == athlete_id,
            health_metric_daily_rollup.c.metric_key.in_(aliases),
        )
    ).scalar_one_or_none()

    if not by_date and last_observed is None:
        return None

    daily = sorted(by_date.values(), key=lambda d: d.local_date)
    return HealthDashboardMetricOut(
        logical_metric="",  # filled in by the caller, which knows the dict key
        last_observed=last_observed,
        daily=daily,
    )


@router.get("/health/dashboard")
def get_health_dashboard(
    athlete_id: Annotated[str, Depends(require_api_key)],
    start_date: date = Query(...),
    end_date: date = Query(...),
    conn: Connection = Depends(get_conn),
) -> HealthDashboardOut:
    metrics = []
    for logical_metric, aliases in LOGICAL_METRICS.items():
        if logical_metric in _OUTLIER_FILTERED_METRICS:
            merged = _body_composition_daily(
                conn,
                athlete_id=athlete_id,
                aliases=aliases,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            merged = _merge_logical_metric(
                conn,
                athlete_id=athlete_id,
                aliases=aliases,
                start_date=start_date,
                end_date=end_date,
            )
        if merged is not None:
            metrics.append(merged.model_copy(update={"logical_metric": logical_metric}))
    return HealthDashboardOut(metrics=metrics)


@router.get("/health/stream")
def get_health_stream(
    athlete_id: Annotated[str, Depends(require_api_key)],
    metric_key: str = Query(...),
    for_date: date = Query(..., alias="date"),
    conn: Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> HealthStreamResponse:
    """One day's worth of an intraday `health_stream` metric (currently only
    `garmin.daily_body_battery.level`, see health/json_parser.py::parse_daily_body_battery_json)
    -- read directly from Parquet with pyarrow, no DuckDB/downsampling needed since a single
    day's readings are small. Empty arrays, not a 404, when there's no stream data for this
    metric/day (a normal state -- e.g. any day before this feature's own live-fetch start date),
    matching the "absent data is a normal state" convention used elsewhere (e.g.
    ActivityWeatherOut.available). More than one matching `health_stream` row (different
    sources writing the same metric_key/month) is merged rather than assumed impossible.
    """
    local_date = for_date.isoformat()
    year_month = local_date[:7]
    rows = conn.execute(
        select(health_stream.c.parquet_path).where(
            health_stream.c.athlete_id == athlete_id,
            health_stream.c.metric_key == metric_key,
            health_stream.c.year_month == year_month,
        )
    ).fetchall()
    if not rows:
        return HealthStreamResponse(
            metric_key=metric_key, local_date=local_date, timestamps=[], values=[]
        )

    day_start = datetime(for_date.year, for_date.month, for_date.day, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    merged: dict[datetime, float] = {}
    for row in rows:
        parquet_path = settings.parquet_dir / row.parquet_path
        if not parquet_path.exists():
            continue
        table = pq.read_table(parquet_path)
        for ts, value in zip(
            table.column("timestamp_utc").to_pylist(), table.column("value").to_pylist(),
            strict=True,
        ):
            if day_start <= ts < day_end:
                merged[ts] = value

    ordered = sorted(merged.items())
    return HealthStreamResponse(
        metric_key=metric_key,
        local_date=local_date,
        timestamps=[ts for ts, _ in ordered],
        values=[v for _, v in ordered],
    )
