"""GET /performance -- reads performance_daily_rollup only, no request-time computation
(CLAUDE.md's rollup mandate). See performance_rollup.py's own docstring for the model.

GET /performance/vo2max-analysis is the one deliberate exception in this file -- see
vo2max_analysis.py's own docstring for why a tiny, occasional diagnostic lookup doesn't fall
under that mandate, the same "bounded, occasional lookup" exception /activities/needs-trim and
/activities/possible-duplicates (api/routers/activities.py) already establish.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.performance import (
    PerformanceDailyRollupOut,
    Vo2maxContributorOut,
    Vo2maxFactorAnalysisOut,
)
from perseverer.db.schema import performance_daily_rollup
from perseverer.vo2max_analysis import Vo2maxContributor, compute_vo2max_factor_analysis

router = APIRouter()


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
