"""GET /performance -- reads performance_daily_rollup only, no request-time computation
(CLAUDE.md's rollup mandate). See performance_rollup.py's own docstring for the model.

GET /performance/vo2max-analysis, GET /performance/pace-hr-zones, and GET
/performance/race-readiness are the deliberate exceptions in this file -- see
vo2max_analysis.py's/pace_hr_zones.py's/race_readiness.py's own docstrings for why a tiny,
occasional diagnostic lookup doesn't fall under that mandate, the same "bounded, occasional
lookup" exception /activities/needs-trim and /activities/possible-duplicates
(api/routers/activities.py) already establish.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, cast

import duckdb
from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, get_duckdb, require_api_key
from perseverer.api.schemas.performance import (
    ActivityRefOut,
    PaceHrZoneOut,
    PaceHrZonesOut,
    PerformanceCurveOut,
    PerformanceCurvePointOut,
    PerformanceDailyRollupOut,
    RaceReadinessOut,
    RaceReadinessPointOut,
    RaceReadinessWeekOut,
    Vo2maxContributorOut,
    Vo2maxFactorAnalysisOut,
    ZoneRunSampleOut,
)
from perseverer.config import Settings, get_settings
from perseverer.db.schema import performance_daily_rollup
from perseverer.pace_hr_zones import ActivityRef, PaceHrZone, compute_pace_hr_zones
from perseverer.performance_curve import CurvePoint, Metric, compute_performance_curve
from perseverer.race_readiness import ReadinessPoint, WeekValue, compute_race_readiness
from perseverer.vo2max_analysis import (
    Vo2maxContributor,
    Vo2maxFactorAnalysis,
    compute_vo2max_factor_analysis,
)

router = APIRouter()


def _readiness_point_out(p: ReadinessPoint) -> RaceReadinessPointOut:
    return RaceReadinessPointOut(
        as_of=p.as_of.isoformat(),
        weekly_distance_compliance_pct=round(p.weekly_distance_compliance * 100, 1),
        long_run_compliance_pct=round(p.long_run_compliance * 100, 1),
        readiness_pct=round(p.readiness * 100, 1),
    )


def _readiness_week_out(w: WeekValue) -> RaceReadinessWeekOut:
    return RaceReadinessWeekOut(week_start=w.week_start.isoformat(), distance_m=w.distance_m)


def _contributor_out(c: Vo2maxContributor) -> Vo2maxContributorOut:
    return Vo2maxContributorOut(
        activity_id=c.activity_id,
        local_date=c.local_date,
        name=c.name,
        sport=c.sport,
        distance_m=c.distance_m,
        duration_s=c.duration_s,
        vdot=c.vdot,
    )


def _vo2max_out(analysis: Vo2maxFactorAnalysis) -> Vo2maxFactorAnalysisOut:
    return Vo2maxFactorAnalysisOut(
        as_of=analysis.as_of,
        window_start=analysis.window_start,
        window_end=analysis.window_end,
        rolling_vdot=analysis.rolling_vdot,
        driving_activity=(
            _contributor_out(analysis.driving_activity)
            if analysis.driving_activity is not None
            else None
        ),
        other_contributors=[_contributor_out(c) for c in analysis.other_contributors],
        expires_on=analysis.expires_on,
        days_since_last_qualifying_run=analysis.days_since_last_qualifying_run,
        missing=analysis.missing,
    )


def _activity_ref_out(a: ActivityRef) -> ActivityRefOut:
    return ActivityRefOut(
        activity_id=a.activity_id,
        local_date=a.local_date,
        name=a.name,
        sport=a.sport,
        distance_m=a.distance_m,
        duration_s=a.duration_s,
    )


def _zone_out(z: PaceHrZone) -> PaceHrZoneOut:
    return PaceHrZoneOut(
        number=z.number,
        label=z.label,
        description=z.description,
        pace_fast_s_per_km=z.pace_fast_s_per_km,
        pace_slow_s_per_km=z.pace_slow_s_per_km,
        hr_low_bpm=z.hr_low_bpm,
        hr_high_bpm=z.hr_high_bpm,
        hr_source=z.hr_source,
        qualifying_run_count=z.qualifying_run_count,
        sample_runs=[
            ZoneRunSampleOut(
                activity_id=r.activity_id,
                local_date=r.local_date,
                name=r.name,
                sport=r.sport,
                distance_m=r.distance_m,
                duration_s=r.duration_s,
                pace_s_per_km=r.pace_s_per_km,
                avg_hr_bpm=r.avg_hr_bpm,
            )
            for r in z.sample_runs
        ],
    )


@router.get("/performance")
def get_performance(
    athlete_id: Annotated[str, Depends(require_api_key)],
    start_date: date = Query(...),
    end_date: date = Query(...),
    conn: Connection = Depends(get_conn),
) -> list[PerformanceDailyRollupOut]:
    rows = conn.execute(
        select(performance_daily_rollup)
        .where(
            performance_daily_rollup.c.athlete_id == athlete_id,
            performance_daily_rollup.c.local_date >= start_date.isoformat(),
            performance_daily_rollup.c.local_date <= end_date.isoformat(),
        )
        .order_by(performance_daily_rollup.c.local_date)
    ).fetchall()
    return [
        PerformanceDailyRollupOut(
            local_date=r.local_date,
            rolling_vdot=r.rolling_vdot,
            max_hr_bpm=r.max_hr_bpm,
            max_hr_source=r.max_hr_source,
            threshold_pace_s_per_km=r.threshold_pace_s_per_km,
            threshold_hr_bpm=r.threshold_hr_bpm,
            threshold_hr_source=r.threshold_hr_source,
            aerobic_threshold_pace_s_per_km=r.aerobic_threshold_pace_s_per_km,
            aerobic_threshold_hr_bpm=r.aerobic_threshold_hr_bpm,
            aerobic_threshold_hr_source=r.aerobic_threshold_hr_source,
            predicted_5k_s=r.predicted_5k_s,
            predicted_10k_s=r.predicted_10k_s,
            predicted_half_marathon_s=r.predicted_half_marathon_s,
            predicted_marathon_s=r.predicted_marathon_s,
        )
        for r in rows
    ]


@router.get("/performance/vo2max-analysis")
def get_vo2max_factor_analysis(
    athlete_id: Annotated[str, Depends(require_api_key)],
    as_of: date | None = Query(None),
    conn: Connection = Depends(get_conn),
) -> Vo2maxFactorAnalysisOut:
    resolved_as_of = as_of if as_of is not None else datetime.now(UTC).date()
    analysis = compute_vo2max_factor_analysis(conn, athlete_id=athlete_id, as_of=resolved_as_of)
    return _vo2max_out(analysis)


@router.get("/performance/pace-hr-zones")
def get_pace_hr_zones(
    athlete_id: Annotated[str, Depends(require_api_key)],
    as_of: date | None = Query(None),
    conn: Connection = Depends(get_conn),
) -> PaceHrZonesOut:
    """The complete 5-zone pace + heart-rate table (Recovery/Basic Endurance/Aerobic Threshold/
    Lactate Threshold/VO2 Max), built from the athlete's entire running history -- see
    pace_hr_zones.py's own module docstring for the model and its literature sources."""
    resolved_as_of = as_of if as_of is not None else datetime.now(UTC).date()
    result = compute_pace_hr_zones(conn, athlete_id=athlete_id, as_of=resolved_as_of)
    return PaceHrZonesOut(
        as_of=result.as_of,
        profile_vdot=result.profile_vdot,
        profile_vdot_activity=(
            _activity_ref_out(result.profile_vdot_activity)
            if result.profile_vdot_activity is not None
            else None
        ),
        profile_max_hr_bpm=result.profile_max_hr_bpm,
        profile_max_hr_source=result.profile_max_hr_source,
        zones=[_zone_out(z) for z in result.zones],
        missing=result.missing,
    )


@router.get("/performance/race-readiness")
def get_race_readiness(
    athlete_id: Annotated[str, Depends(require_api_key)],
    race_id: int | None = Query(None),
    as_of: date | None = Query(None),
    conn: Connection = Depends(get_conn),
) -> RaceReadinessOut:
    """`race_id` defaults to the athlete's own nearest upcoming running race. `available=False`
    (never a fabricated readiness) when there's no such race, or `race_id` doesn't belong to this
    athlete -- see race_readiness.py's own module docstring for the full model."""
    resolved_as_of = as_of if as_of is not None else datetime.now(UTC).date()
    readiness = compute_race_readiness(
        conn, athlete_id=athlete_id, as_of=resolved_as_of, race_id=race_id
    )
    if readiness is None:
        return RaceReadinessOut(available=False)
    return RaceReadinessOut(
        available=True,
        race_id=readiness.race_id,
        race_name=readiness.race_name,
        race_local_date=readiness.race_local_date,
        race_distance_m=readiness.race_distance_m,
        weekly_distance_target_m=readiness.weekly_distance_target_m,
        long_run_target_m=readiness.long_run_target_m,
        as_of=readiness.current.as_of.isoformat(),
        current=_readiness_point_out(readiness.current),
        predicted_duration_s=readiness.predicted_duration_s,
        history=[_readiness_point_out(p) for p in readiness.history],
        weekly_distance_series=[_readiness_week_out(w) for w in readiness.weekly_distance_series],
        long_run_series=[_readiness_week_out(w) for w in readiness.long_run_series],
    )


def _curve_point_out(p: CurvePoint) -> PerformanceCurvePointOut:
    return PerformanceCurvePointOut(
        duration_s=p.duration_s, value=p.value, activity_id=p.activity_id, local_date=p.local_date
    )


@router.get("/performance/curve")
def get_performance_curve(
    athlete_id: Annotated[str, Depends(require_api_key)],
    metric: Annotated[str, Query(pattern="^(pace|gap|heart_rate)$")],
    start_date: date = Query(...),
    end_date: date = Query(...),
    sports: str | None = Query(None),
    conn: Connection = Depends(get_conn),
    duckdb_conn: duckdb.DuckDBPyConnection = Depends(get_duckdb),
    settings: Settings = Depends(get_settings),
) -> PerformanceCurveOut:
    """The best sustained average value for each of a fixed set of durations (1s-2h), across
    every qualifying activity in `[start_date, end_date]` -- see performance_curve.py's own
    module docstring for the full model (the sliding-window search, gap disqualification, and why
    the athlete's own already-computed threshold pace/HR are returned alongside as reference
    values, never blended into the curve itself). `sports` (comma-separated) scopes which sports
    feed a `heart_rate` curve -- ignored entirely for `pace`/`gap`, which always mean running.
    `available=False` (never a fabricated curve) when nothing in range has the stream channel(s)
    this metric needs at all."""
    sport_list = sports.split(",") if sports else None
    curve = compute_performance_curve(
        conn,
        duckdb_conn,
        settings.parquet_dir,
        athlete_id=athlete_id,
        metric=cast(Metric, metric),
        start_date=start_date,
        end_date=end_date,
        sports=sport_list,
        as_of=datetime.now(UTC).date(),
    )
    return PerformanceCurveOut(
        available=bool(curve.points),
        metric=curve.metric,
        points=[_curve_point_out(p) for p in curve.points],
        threshold_pace_s_per_km=curve.threshold_pace_s_per_km,
        aerobic_threshold_pace_s_per_km=curve.aerobic_threshold_pace_s_per_km,
        threshold_hr_bpm=curve.threshold_hr_bpm,
        aerobic_threshold_hr_bpm=curve.aerobic_threshold_hr_bpm,
        max_hr_bpm=curve.max_hr_bpm,
    )
