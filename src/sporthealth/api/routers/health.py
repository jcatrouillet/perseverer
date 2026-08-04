"""GET /health/observations."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, func, select

from sporthealth.api.dependencies import get_conn, require_api_key
from sporthealth.api.schemas.common import Page, to_utc
from sporthealth.api.schemas.health import HealthObservationOut
from sporthealth.db.schema import health_observation

router = APIRouter()


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
