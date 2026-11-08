"""GET/POST/DELETE /dexa-scans -- athlete-entered DEXA body-composition scans. See
api/schemas/dexa.py and db/schema.py::dexa_scan_result. A scan is addressed by its date."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.dexa import DexaScanIn, DexaScanOut, DexaValueOut
from perseverer.db.schema import dexa_scan_result

router = APIRouter()


def _scans(conn: Connection, athlete_id: str, local_date: str | None = None) -> list[DexaScanOut]:
    query = select(dexa_scan_result).where(dexa_scan_result.c.athlete_id == athlete_id)
    if local_date is not None:
        query = query.where(dexa_scan_result.c.local_date == local_date)
    rows = conn.execute(
        query.order_by(
            dexa_scan_result.c.local_date.desc(),
            dexa_scan_result.c.region,
            dexa_scan_result.c.metric,
        )
    ).fetchall()
    by_date: dict[str, DexaScanOut] = {}
    for row in rows:
        scan = by_date.setdefault(
            row.local_date, DexaScanOut(local_date=row.local_date, provider=row.provider, values=[])
        )
        scan.values.append(
            DexaValueOut(region=row.region, metric=row.metric, value_num=row.value_num)
        )
    return list(by_date.values())


@router.get("/dexa-scans")
def list_dexa_scans(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[DexaScanOut]:
    """Every scan, newest first."""
    return _scans(conn, athlete_id)


@router.post("/dexa-scans")
def post_dexa_scan(
    payload: DexaScanIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> DexaScanOut:
    """Records a scan. A value already stored for the same date, region and metric is replaced,
    so posting the same report twice changes nothing."""
    now = datetime.now(UTC).replace(tzinfo=None)
    conn.execute(
        dexa_scan_result.delete().where(
            dexa_scan_result.c.athlete_id == athlete_id,
            dexa_scan_result.c.local_date == payload.local_date,
            dexa_scan_result.c.region.in_({v.region for v in payload.values}),
            dexa_scan_result.c.metric.in_({v.metric for v in payload.values}),
        )
    )
    seen: dict[tuple[str, str], float] = {}
    for v in payload.values:
        seen[(v.region, v.metric)] = v.value_num
    conn.execute(
        dexa_scan_result.insert(),
        [
            {
                "athlete_id": athlete_id,
                "local_date": payload.local_date,
                "region": region,
                "metric": metric,
                "value_num": value,
                "provider": payload.provider,
                "created_at": now,
            }
            for (region, metric), value in seen.items()
        ],
    )
    conn.commit()
    return _scans(conn, athlete_id, payload.local_date)[0]


@router.delete("/dexa-scans/{local_date}")
def delete_dexa_scan(
    local_date: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> None:
    """Deletes every value of the scan taken on `local_date`."""
    result = conn.execute(
        dexa_scan_result.delete().where(
            dexa_scan_result.c.athlete_id == athlete_id,
            dexa_scan_result.c.local_date == local_date,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="no DEXA scan on that date")
    conn.commit()
