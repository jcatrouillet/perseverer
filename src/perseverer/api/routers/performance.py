"""GET /performance -- reads performance_daily_rollup only, no request-time computation
(CLAUDE.md's rollup mandate). See performance_rollup.py's own docstring for the model.

GET /performance/vo2max-analysis, GET /performance/threshold-analysis, and GET
/performance/race-readiness are the deliberate exceptions in this file -- see
vo2max_analysis.py's/threshold_analysis.py's/race_readiness.py's own docstrings for why a tiny,
occasional diagnostic lookup doesn't fall under that mandate, the same "bounded, occasional
lookup" exception /activities/needs-trim and /activities/possible-duplicates
(api/routers/activities.py) already establish.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.performance import (
    ActivityRefOut,
    PerformanceDailyRollupOut,
    RaceReadinessOut,
    RaceReadinessPointOut,
    RaceReadinessWeekOut,
    ThresholdFactorAnalysisOut,
    ThresholdHrBreakdownOut,
    ThresholdHrContributorOut,
    Vo2maxContributorOut,
    Vo2maxFactorAnalysisOut,
)
from perseverer.db.schema import performance_daily_rollup
from perseverer.race_readiness import ReadinessPoint, WeekValue, compute_race_readiness
from perseverer.threshold_analysis import (
    ActivityRef,
    ThresholdHrBreakdown,
    compute_threshold_factor_analysis,
)
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


def _threshold_hr_out(b: ThresholdHrBreakdown) -> ThresholdHrBreakdownOut:
    return ThresholdHrBreakdownOut(
        threshold_hr_bpm=b.threshold_hr_bpm,
        threshold_hr_source=b.threshold_hr_source,
        reference_pace_s_per_km=b.reference_pace_s_per_km,
        contributors=[
            ThresholdHrContributorOut(
                activity_id=c.activity_id,
                local_date=c.local_date,
                name=c.name,
                sport=c.sport,
                distance_m=c.distance_m,
                duration_s=c.duration_s,
                pace_s_per_km=c.pace_s_per_km,
                avg_hr_bpm=c.avg_hr_bpm,
                is_median=c.is_median,
            )
            for c in b.contributors
        ],
        max_hr_driving_activity=(
            _activity_ref_out(b.max_hr_driving_activity)
            if b.max_hr_driving_activity is not None
            else None
        ),
        missing=b.missing,
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


@router.get("/performance/threshold-analysis")
def get_threshold_factor_analysis(
    athlete_id: Annotated[str, Depends(require_api_key)],
    as_of: date | None = Query(None),
    conn: Connection = Depends(get_conn),
) -> ThresholdFactorAnalysisOut:
    resolved_as_of = as_of if as_of is not None else datetime.now(UTC).date()
    analysis = compute_threshold_factor_analysis(conn, athlete_id=athlete_id, as_of=resolved_as_of)
    return ThresholdFactorAnalysisOut(
        as_of=analysis.as_of,
        vo2max=_vo2max_out(analysis.vo2max),
        anaerobic_threshold_pace_s_per_km=analysis.anaerobic_threshold_pace_s_per_km,
        aerobic_threshold_pace_s_per_km=analysis.aerobic_threshold_pace_s_per_km,
        anaerobic_threshold_hr=_threshold_hr_out(analysis.anaerobic_threshold_hr),
        aerobic_threshold_hr=_threshold_hr_out(analysis.aerobic_threshold_hr),
        max_hr_bpm=analysis.max_hr_bpm,
        max_hr_source=analysis.max_hr_source,
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
