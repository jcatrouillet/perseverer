"""GET /performance -- reads performance_daily_rollup only, no request-time computation
(CLAUDE.md's rollup mandate). See performance_rollup.py's own docstring for the model.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.performance import PerformanceDailyRollupOut
from perseverer.db.schema import performance_daily_rollup

router = APIRouter()


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
