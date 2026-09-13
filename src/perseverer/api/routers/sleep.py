"""GET /sleep."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.common import to_utc
from perseverer.api.schemas.sleep import SleepSessionOut, SleepStageOut
from perseverer.db.schema import sleep_session, sleep_stage

router = APIRouter()

# Preferred source per local_date when a historical backfill and the live incremental sync both
# cover the same night -- e.g. garmin_export's one-time GDPR-export backfill window overlapping
# garmin_connect's rolling sync window (PERSEVERER_GARMIN_ROLLING_WINDOW_DAYS). sleep_session's
# own idempotency key is (athlete_id, local_date, source), so both rows are legitimately
# archived -- this is not an ingestion bug, just two sources describing the same night.
# garmin_connect wins because its total_sleep_s comes from Garmin's own `sleepTimeSeconds` field
# (excludes brief awake periods within the sleep window, see
# health/json_parser.py::parse_daily_sleep_json), while garmin_export's FIT-derived
# total_sleep_s (health/fit_parser.py::_build_sleep) is a raw start-to-end span that overstates
# actual sleep time. Any other source falls back to whichever row was returned first for that
# date.
_SOURCE_PRIORITY = ("garmin_connect", "garmin_export")


def _source_rank(source: str) -> int:
    try:
        return _SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(_SOURCE_PRIORITY)


@router.get("/sleep")
def list_sleep(
    athlete_id: Annotated[str, Depends(require_api_key)],
    start_date: date = Query(...),
    end_date: date = Query(...),
    conn: Connection = Depends(get_conn),
) -> list[SleepSessionOut]:
    rows = conn.execute(
        select(sleep_session)
        .where(
            sleep_session.c.athlete_id == athlete_id,
            sleep_session.c.local_date >= start_date.isoformat(),
            sleep_session.c.local_date <= end_date.isoformat(),
        )
        .order_by(sleep_session.c.local_date)
    ).fetchall()

    # One row per local_date -- see _SOURCE_PRIORITY above for why, and which source wins.
    by_date: dict[str, Any] = {}
    for row in rows:
        existing = by_date.get(row.local_date)
        if existing is None or _source_rank(row.source) < _source_rank(existing.source):
            by_date[row.local_date] = row
    sessions = sorted(by_date.values(), key=lambda row: row.local_date)

    # One batched fetch for every session's stages, instead of one query per session -- with
    # ~1,400+ nights of real history that was ~1,400 individual queries against sleep_stage
    # (itself uncovered by any index on sleep_session_id, so each one was a full scan), the
    # dominant cost in this endpoint by far. See the new ix_sleep_stage_session index.
    stages_by_session_id: dict[int, list[Any]] = {}
    if sessions:
        for stage in conn.execute(
            select(sleep_stage)
            .where(sleep_stage.c.sleep_session_id.in_([s.id for s in sessions]))
            .order_by(sleep_stage.c.sleep_session_id, sleep_stage.c.start_time_utc)
        ).fetchall():
            stages_by_session_id.setdefault(stage.sleep_session_id, []).append(stage)

    out = []
    for s in sessions:
        stages = stages_by_session_id.get(s.id, [])
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
