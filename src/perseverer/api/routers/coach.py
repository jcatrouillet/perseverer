"""GET/PUT/DELETE /coach -- an athlete's coach, and the athletes one coaches. The athlete picks
their coach by username and can stop sharing at any time; the coach then sees the athlete's data
by sending `X-Act-As` (dependencies.py::require_api_key). Linking and unlinking are the athlete's
own acts, so a coach acting for someone never reaches them (coach_may refuses the write).
docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.coach import CoachIn, CoachOut, CoachPersonOut
from perseverer.api.schemas.common import to_utc
from perseverer.db.schema import athlete, coach_link

router = APIRouter()


def _coach_out(conn: Connection, athlete_id: str) -> CoachOut:
    coach_row = conn.execute(
        select(
            athlete.c.id,
            athlete.c.display_name,
            athlete.c.username,
            coach_link.c.created_at,
        )
        .join(coach_link, coach_link.c.coach_athlete_id == athlete.c.id)
        .where(coach_link.c.athlete_id == athlete_id)
    ).first()
    coachee_rows = conn.execute(
        select(
            athlete.c.id,
            athlete.c.display_name,
            athlete.c.username,
            coach_link.c.created_at,
        )
        .join(coach_link, coach_link.c.athlete_id == athlete.c.id)
        .where(coach_link.c.coach_athlete_id == athlete_id)
        .order_by(athlete.c.display_name)
    ).all()

    def person(r: object) -> CoachPersonOut:
        return CoachPersonOut(
            id=r.id,  # type: ignore[attr-defined]
            display_name=r.display_name,  # type: ignore[attr-defined]
            username=r.username,  # type: ignore[attr-defined]
            since=to_utc(r.created_at),  # type: ignore[attr-defined]
        )

    return CoachOut(
        coach=person(coach_row) if coach_row is not None else None,
        coachees=[person(r) for r in coachee_rows],
    )


@router.get("/coach")
def get_coach(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> CoachOut:
    return _coach_out(conn, athlete_id)


@router.put("/coach")
def set_coach(
    payload: CoachIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> CoachOut:
    """Chooses (or replaces) the athlete's coach, who can then view their data and add activities
    and comments."""
    coach_id = conn.execute(
        select(athlete.c.id).where(athlete.c.username == payload.username.strip())
    ).scalar_one_or_none()
    if coach_id is None:
        raise HTTPException(status_code=404, detail="no athlete has this username")
    if coach_id == athlete_id:
        raise HTTPException(status_code=422, detail="you cannot be your own coach")
    now = datetime.now(UTC).replace(tzinfo=None)
    existing = conn.execute(
        select(coach_link.c.coach_athlete_id).where(coach_link.c.athlete_id == athlete_id)
    ).scalar_one_or_none()
    if existing is None:
        conn.execute(
            coach_link.insert().values(
                athlete_id=athlete_id, coach_athlete_id=coach_id, created_at=now
            )
        )
    elif existing != coach_id:
        conn.execute(
            coach_link.update()
            .where(coach_link.c.athlete_id == athlete_id)
            .values(coach_athlete_id=coach_id, created_at=now)
        )
    conn.commit()
    return _coach_out(conn, athlete_id)


@router.delete("/coach", status_code=204)
def stop_sharing(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> Response:
    """The athlete stops sharing with their coach."""
    conn.execute(coach_link.delete().where(coach_link.c.athlete_id == athlete_id))
    conn.commit()
    return Response(status_code=204)


@router.delete("/coach/coachees/{coachee_id}", status_code=204)
def stop_coaching(
    coachee_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> Response:
    """The coach gives the athlete back: ends the link from their side."""
    conn.execute(
        coach_link.delete().where(
            coach_link.c.athlete_id == coachee_id, coach_link.c.coach_athlete_id == athlete_id
        )
    )
    conn.commit()
    return Response(status_code=204)
