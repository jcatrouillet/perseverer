"""GET /insights -- reads the `insight` table only, no request-time computation (matches this
codebase's rollup mandate). Refreshed by insights/engine.py on ingest and once daily (see ADR
0012); this endpoint never recomputes anything itself.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from sporthealth.api.dependencies import get_conn, require_api_key
from sporthealth.api.schemas.common import to_utc
from sporthealth.api.schemas.insights import InsightOut
from sporthealth.db.schema import insight

router = APIRouter()


@router.get("/insights")
def list_insights(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    kind: str | None = Query(None),
    window: str | None = Query(None),
) -> list[InsightOut]:
    query = select(insight).where(insight.c.athlete_id == athlete_id)
    if kind is not None:
        query = query.where(insight.c.kind == kind)
    if window is not None:
        query = query.where(insight.c.window == window)
    rows = conn.execute(query.order_by(insight.c.kind, insight.c.window)).fetchall()
    return [
        InsightOut(
            kind=r.kind,
            window=r.window,
            title=r.title,
            detail=json.loads(r.detail),
            value_num=r.value_num,
            metric_key=r.metric_key,
            sport_family=r.sport_family,
            activity_id=r.activity_id,
            local_date=r.local_date,
            computed_at=to_utc(r.computed_at),
        )
        for r in rows
    ]
