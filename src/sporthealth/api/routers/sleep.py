"""GET /sleep."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from sporthealth.api.dependencies import get_conn, require_api_key
from sporthealth.api.schemas.common import to_utc
from sporthealth.api.schemas.sleep import SleepSessionOut, SleepStageOut
from sporthealth.db.schema import sleep_session, sleep_stage

router = APIRouter()


@router.get("/sleep")
def list_sleep(
    athlete_id: Annotated[str, Depends(require_api_key)],
    start_date: date = Query(...),
    end_date: date = Query(...),
    conn: Connection = Depends(get_conn),
) -> list[SleepSessionOut]:
    sessions = conn.execute(
        select(sleep_session)
        .where(
            sleep_session.c.athlete_id == athlete_id,
            sleep_session.c.local_date >= start_date.isoformat(),
            sleep_session.c.local_date <= end_date.isoformat(),
        )
        .order_by(sleep_session.c.local_date)
    ).fetchall()

    out = []
    for s in sessions:
        stages = conn.execute(
            select(sleep_stage)
            .where(sleep_stage.c.sleep_session_id == s.id)
            .order_by(sleep_stage.c.start_time_utc)
        ).fetchall()
        out.append(
            SleepSessionOut(
                local_date=s.local_date,
                start_time_utc=to_utc(s.start_time_utc),
                end_time_utc=to_utc(s.end_time_utc),
                total_sleep_s=s.total_sleep_s,
                sleep_score=s.sleep_score,
                source=s.source,
                stages=[
                    SleepStageOut(
                        stage=st.stage,
                        start_time_utc=to_utc(st.start_time_utc),
                        end_time_utc=to_utc(st.end_time_utc),
                    )
                    for st in stages
                ],
            )
        )
    return out
