"""PUT /kaya-climbs/{climb_kaya_id}/note -- the athlete's own note on a Kaya route.

A route is a Kaya *problem* (its id is stable across every session it is repeated in), so the one
note follows it everywhere: it comes back as `SplitOut.note` on every row of that route in every
activity (see activities.py), and the Routes table shows it on hover. Garmin-only rows have no such
identity and carry no notes. Stored in `kaya_climb_note` -- athlete input, never wiped by a rebuild.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Connection, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.db.schema import kaya_ascent, kaya_attempt, kaya_climb_note, split

router = APIRouter()

MAX_NOTE_CHARS = 2000


class ClimbNoteIn(BaseModel):
    # An empty (or whitespace-only) note removes the note.
    note: str = Field(max_length=MAX_NOTE_CHARS)


class ClimbNoteOut(BaseModel):
    climb_kaya_id: str
    note: str | None  # null once removed


def _route_is_known(conn: Connection, athlete_id: str, climb_kaya_id: str) -> bool:
    """A note can only be attached to a route this athlete actually has (a Kaya send, an unsent
    attempt, or a split the import wrote) -- never to an arbitrary string."""
    for table, column in (
        (kaya_ascent, kaya_ascent.c.climb_kaya_id),
        (kaya_attempt, kaya_attempt.c.climb_kaya_id),
        (split, split.c.climb_kaya_id),
    ):
        found = conn.execute(
            select(table.c.id)
            .where(table.c.athlete_id == athlete_id, column == climb_kaya_id)
            .limit(1)
        ).first()
        if found is not None:
            return True
    return False


@router.put("/kaya-climbs/{climb_kaya_id}/note")
def put_climb_note(
    climb_kaya_id: str,
    payload: ClimbNoteIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> ClimbNoteOut:
    if not _route_is_known(conn, athlete_id, climb_kaya_id):
        raise HTTPException(status_code=404, detail="route not found")
    text = payload.note.strip()
    if not text:
        conn.execute(
            kaya_climb_note.delete().where(
                kaya_climb_note.c.athlete_id == athlete_id,
                kaya_climb_note.c.climb_kaya_id == climb_kaya_id,
            )
        )
        conn.commit()
        return ClimbNoteOut(climb_kaya_id=climb_kaya_id, note=None)
    now = datetime.now(UTC).replace(tzinfo=None)
    stmt = sqlite_insert(kaya_climb_note).values(
        athlete_id=athlete_id, climb_kaya_id=climb_kaya_id, note=text, updated_at=now
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["athlete_id", "climb_kaya_id"],
            set_={"note": text, "updated_at": now},
        )
    )
    conn.commit()
    return ClimbNoteOut(climb_kaya_id=climb_kaya_id, note=text)
