"""GET/POST/PUT/DELETE /blood-tests -- athlete-entered blood test results, one row per marker
per draw. See api/schemas/blood_tests.py for the request/response shapes and db/schema.py::
blood_test_result for storage. `POST /blood-tests/batch` is the primary write path (a whole
panel, many markers, one draw date, in one call); the single-marker `POST`/`PUT`/`DELETE` routes
exist for adding one more marker to an existing draw or correcting a single value afterward.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Connection, Row, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.blood_tests import (
    BloodTestBatchIn,
    BloodTestResultIn,
    BloodTestResultOut,
)
from perseverer.db.schema import blood_test_result

router = APIRouter()


def _to_out(row: Row) -> BloodTestResultOut:  # type: ignore[type-arg]
    return BloodTestResultOut(
        id=row.id,
        local_date=row.local_date,
        marker=row.marker,
        value_num=row.value_num,
        unit=row.unit,
        reference_low=row.reference_low,
        reference_high=row.reference_high,
        lab_name=row.lab_name,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/blood-tests")
def list_blood_tests(
    athlete_id: Annotated[str, Depends(require_api_key)],
    start_date: Annotated[str, Query()],
    end_date: Annotated[str, Query()],
    conn: Connection = Depends(get_conn),
) -> list[BloodTestResultOut]:
    rows = conn.execute(
        select(blood_test_result)
        .where(
            blood_test_result.c.athlete_id == athlete_id,
            blood_test_result.c.local_date >= start_date,
            blood_test_result.c.local_date <= end_date,
        )
        .order_by(blood_test_result.c.local_date.desc(), blood_test_result.c.marker)
    ).fetchall()
    return [_to_out(row) for row in rows]


@router.get("/blood-tests/{result_id}")
def get_blood_test_result(
    result_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> BloodTestResultOut:
    row = conn.execute(
        select(blood_test_result).where(
            blood_test_result.c.id == result_id, blood_test_result.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="blood test result not found")
    return _to_out(row)


@router.post("/blood-tests")
def post_blood_test_result(
    payload: BloodTestResultIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> BloodTestResultOut:
    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage (ADR 0002)
    result = conn.execute(
        blood_test_result.insert().values(
            athlete_id=athlete_id,
            local_date=payload.local_date,
            marker=payload.marker,
            value_num=payload.value_num,
            unit=payload.unit,
            reference_low=payload.reference_low,
            reference_high=payload.reference_high,
            lab_name=payload.lab_name,
            notes=payload.notes,
            created_at=now,
            updated_at=now,
        )
    )
    assert result.inserted_primary_key is not None
    conn.commit()
    return get_blood_test_result(result.inserted_primary_key[0], athlete_id, conn)


@router.post("/blood-tests/batch")
def post_blood_test_batch(
    payload: BloodTestBatchIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> list[BloodTestResultOut]:
    now = datetime.now(UTC).replace(tzinfo=None)
    ids: list[int] = []
    for marker in payload.results:
        result = conn.execute(
            blood_test_result.insert().values(
                athlete_id=athlete_id,
                local_date=payload.local_date,
                marker=marker.marker,
                value_num=marker.value_num,
                unit=marker.unit,
                reference_low=marker.reference_low,
                reference_high=marker.reference_high,
                lab_name=payload.lab_name,
                notes=payload.notes,
                created_at=now,
                updated_at=now,
            )
        )
        assert result.inserted_primary_key is not None
        ids.append(result.inserted_primary_key[0])
    conn.commit()
    rows = conn.execute(select(blood_test_result).where(blood_test_result.c.id.in_(ids))).fetchall()
    by_id = {row.id: row for row in rows}
    return [_to_out(by_id[i]) for i in ids]


@router.put("/blood-tests/{result_id}")
def put_blood_test_result(
    result_id: int,
    payload: BloodTestResultIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> BloodTestResultOut:
    existing = conn.execute(
        select(blood_test_result.c.id).where(
            blood_test_result.c.id == result_id, blood_test_result.c.athlete_id == athlete_id
        )
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="blood test result not found")

    conn.execute(
        blood_test_result.update()
        .where(blood_test_result.c.id == result_id)
        .values(
            local_date=payload.local_date,
            marker=payload.marker,
            value_num=payload.value_num,
            unit=payload.unit,
            reference_low=payload.reference_low,
            reference_high=payload.reference_high,
            lab_name=payload.lab_name,
            notes=payload.notes,
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    conn.commit()
    return get_blood_test_result(result_id, athlete_id, conn)


@router.delete("/blood-tests/{result_id}")
def delete_blood_test_result(
    result_id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> None:
    result = conn.execute(
        blood_test_result.delete().where(
            blood_test_result.c.id == result_id, blood_test_result.c.athlete_id == athlete_id
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="blood test result not found")
    conn.commit()


@router.delete("/blood-tests/by-date/{local_date}")
def delete_blood_test_panel(
    local_date: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> None:
    """Deletes every marker recorded on `local_date` -- the whole panel at once, rather than the
    athlete removing each of a panel's markers one by one."""
    result = conn.execute(
        blood_test_result.delete().where(
            blood_test_result.c.athlete_id == athlete_id,
            blood_test_result.c.local_date == local_date,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="no blood test results on that date")
    conn.commit()
