"""GET /fitness -- reads fitness_daily_rollup only, no request-time computation (CLAUDE.md's
rollup mandate). See docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.fitness import FitnessDailyRollupOut
from perseverer.db.schema import fitness_daily_rollup

router = APIRouter()


@router.get("/fitness")
def get_fitness(
    athlete_id: Annotated[str, Depends(require_api_key)],
    start_date: date = Query(...),
    end_date: date = Query(...),
    conn: Connection = Depends(get_conn),
) -> list[FitnessDailyRollupOut]:
    rows = conn.execute(
        select(fitness_daily_rollup)
        .where(
            fitness_daily_rollup.c.athlete_id == athlete_id,
            fitness_daily_rollup.c.local_date >= start_date.isoformat(),
            fitness_daily_rollup.c.local_date <= end_date.isoformat(),
        )
        .order_by(fitness_daily_rollup.c.local_date)
    ).fetchall()
    return [
        FitnessDailyRollupOut(
            local_date=r.local_date,
            training_load=r.training_load,
            ctl=r.ctl,
            atl=r.atl,
            tsb=r.tsb,
        )
        for r in rows
    ]
