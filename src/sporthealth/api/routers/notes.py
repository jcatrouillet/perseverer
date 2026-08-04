"""POST/GET /notes -- the agent-writable write path CLAUDE.md's mission statement calls for.
Scoped to activities and days only. See docs/adr/0006-phase-3-read-api-and-rollups.md
decision 7.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Connection, select

from sporthealth.api.dependencies import get_conn, require_api_key
from sporthealth.api.schemas.common import to_utc
from sporthealth.api.schemas.notes import NoteCreate, NoteOut
from sporthealth.db.schema import activity, note
from sporthealth.db.seed import DEFAULT_ATHLETE_ID

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/notes", status_code=status.HTTP_201_CREATED)
def create_note(payload: NoteCreate, conn: Connection = Depends(get_conn)) -> NoteOut:
    if payload.entity_type == "activity":
        exists = conn.execute(
            select(activity.c.id).where(
                activity.c.id == payload.entity_id,
                activity.c.athlete_id == DEFAULT_ATHLETE_ID,
                activity.c.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=404, detail="activity not found")
    # entity_type == "day": no existence check -- a day is just a date, not a row that can be
    # missing.

    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage (ADR 0002)
    result = conn.execute(
        note.insert().values(
            athlete_id=DEFAULT_ATHLETE_ID,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            body=payload.body,
            author=payload.author,
            created_at=now,
            updated_at=now,
        )
    )
    conn.commit()
    assert result.inserted_primary_key is not None
    note_id = result.inserted_primary_key[0]
    assert isinstance(note_id, int)

    return NoteOut(
        id=note_id,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        body=payload.body,
        author=payload.author,
        created_at=to_utc(now),
        updated_at=to_utc(now),
    )


@router.get("/notes")
def list_notes(
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    conn: Connection = Depends(get_conn),
) -> list[NoteOut]:
    rows = conn.execute(
        select(note)
        .where(
            note.c.athlete_id == DEFAULT_ATHLETE_ID,
            note.c.entity_type == entity_type,
            note.c.entity_id == entity_id,
        )
        .order_by(note.c.created_at)
    ).fetchall()
    return [
        NoteOut(
            id=r.id,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            body=r.body,
            author=r.author,
            created_at=to_utc(r.created_at),
            updated_at=to_utc(r.updated_at),
        )
        for r in rows
    ]
