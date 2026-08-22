"""GET/PUT /settings/hr-zones -- an athlete's own configured HR training zones, derived from
three reference points (max/threshold/resting HR). See api/schemas/settings.py and
db/schema.py::athlete_hr_zone_config for the shape and hr_zones.py for the derivation formula.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.settings import HrZoneConfigIn, HrZoneConfigOut
from perseverer.db.schema import athlete_hr_zone_config

router = APIRouter()


@router.get("/settings/hr-zones")
def get_hr_zone_config(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> HrZoneConfigOut:
    row = conn.execute(
        select(athlete_hr_zone_config).where(athlete_hr_zone_config.c.athlete_id == athlete_id)
    ).fetchone()
    if row is None:
        return HrZoneConfigOut.from_inputs(None, None, None)
    return HrZoneConfigOut.from_inputs(row.max_hr_bpm, row.threshold_hr_bpm, row.resting_hr_bpm)


@router.put("/settings/hr-zones")
def set_hr_zone_config(
    payload: HrZoneConfigIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> HrZoneConfigOut:
    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage (ADR 0002)
    existing = conn.execute(
        select(athlete_hr_zone_config.c.athlete_id).where(
            athlete_hr_zone_config.c.athlete_id == athlete_id
        )
    ).scalar_one_or_none()
    values = {
        "max_hr_bpm": payload.max_hr_bpm,
        "threshold_hr_bpm": payload.threshold_hr_bpm,
        "resting_hr_bpm": payload.resting_hr_bpm,
        "updated_at": now,
    }
    if existing is None:
        conn.execute(athlete_hr_zone_config.insert().values(athlete_id=athlete_id, **values))
    else:
        conn.execute(
            athlete_hr_zone_config.update()
            .where(athlete_hr_zone_config.c.athlete_id == athlete_id)
            .values(**values)
        )
    conn.commit()
    return HrZoneConfigOut.from_inputs(
        payload.max_hr_bpm, payload.threshold_hr_bpm, payload.resting_hr_bpm
    )
