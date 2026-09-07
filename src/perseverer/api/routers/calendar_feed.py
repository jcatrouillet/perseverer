"""`GET /share/calendar/{token}.ics` -- the public, unauthenticated iCalendar feed itself. Mounted
with `prefix="/share"` (api/main.py) so it reuses the existing `/share/` nginx prefix rule
(docker/nginx.conf) with no infra change -- public by omission, same convention share.py already
established, kept in its own file since this is a structurally distinct concern (one standing
per-athlete secret + `text/calendar`) from share.py's own many-one-off-shares + HTML model. See
calendar_feed.py for the token/feed-building logic and api/routers/settings.py for the
authenticated create/rotate/revoke routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import Connection

from perseverer.api.dependencies import get_conn
from perseverer.calendar_feed import build_ics_feed, resolve_feed_token

router = APIRouter()


@router.get("/calendar/{token}.ics")
def get_calendar_feed(token: str, conn: Connection = Depends(get_conn)) -> Response:
    resolved = resolve_feed_token(conn, token)
    if resolved is None:
        raise HTTPException(status_code=404, detail="calendar feed not found")
    athlete_id, timezone_name = resolved
    ics_bytes = build_ics_feed(conn, athlete_id=athlete_id, timezone_name=timezone_name)
    return Response(content=ics_bytes, media_type="text/calendar; charset=utf-8")
