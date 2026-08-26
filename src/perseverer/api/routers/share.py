"""Share links: an athlete-issued token granting unauthenticated, read-only access to one
activity or one summary period. Two routers, deliberately:

- `management_router` (mounted under `/api/v1`, normal `Depends(require_api_key)` auth) --
  create/revoke, for the athlete's own use from within the app.
- `router` (mounted with NO prefix, no auth at all) -- `GET /share/{token}`, the whole reason
  this module exists. Public by omission, not by any special-cased middleware bypass -- same
  exemption mechanism `/healthz`/`/version`/`/auth/login` already use (see api/main.py's own
  docstring): a route is "public" purely by never taking the `Depends(require_api_key)`
  parameter. Returns server-rendered HTML (sharing.py), not JSON -- both the visible page and
  the `<meta property="og:...">` tags social platforms read come from the same render, so
  there's no risk of a preview card disagreeing with the page it links to.

nginx (docker/nginx.conf) proxies the public `/share/*` path from the frontend's own origin to
this API container -- the reverse proxy in front of both containers splits by port, not path
(see docs/DEPLOY.md), so a same-origin share link needs that one config-only forward.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, select

from perseverer.api.dependencies import get_conn, require_api_key
from perseverer.api.schemas.share import RevokeShareOut, ShareLinkOut
from perseverer.config import Settings, get_settings
from perseverer.db.schema import activity
from perseverer.sharing import (
    VALID_PERIOD_TYPES,
    create_share_link,
    render_activity_share_html,
    render_period_share_html,
    render_unavailable_html,
    resolve_share_token,
    revoke_share_link,
)

management_router = APIRouter()
router = APIRouter()


def _public_base_url(request: Request, settings: Settings) -> str:
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


@management_router.post("/activities/{activity_id}/share")
def post_activity_share(
    activity_id: str,
    request: Request,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> ShareLinkOut:
    exists = conn.execute(
        select(activity.c.id).where(
            activity.c.id == activity_id,
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="activity not found")

    new_id, token = create_share_link(
        conn, athlete_id=athlete_id, target_type="activity", target_id=activity_id
    )
    conn.commit()
    return ShareLinkOut(id=new_id, url=f"{_public_base_url(request, settings)}/share/{token}")


@management_router.post("/periods/{period_type}/share")
def post_period_share(
    period_type: Literal["week", "month", "year", "all"],
    request: Request,
    athlete_id: Annotated[str, Depends(require_api_key)],
    period_start: Annotated[str | None, Query()] = None,
    conn: Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> ShareLinkOut:
    if period_type == "all":
        target_id = "all"
    else:
        if not period_start:
            raise HTTPException(
                status_code=422, detail="period_start is required unless period_type is 'all'"
            )
        target_id = f"{period_type}:{period_start}"

    new_id, token = create_share_link(
        conn, athlete_id=athlete_id, target_type="period", target_id=target_id
    )
    conn.commit()
    return ShareLinkOut(id=new_id, url=f"{_public_base_url(request, settings)}/share/{token}")


@management_router.post("/share/{id}/revoke")
def post_revoke_share(
    id: int,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> RevokeShareOut:
    revoked = revoke_share_link(conn, athlete_id=athlete_id, id=id)
    conn.commit()
    return RevokeShareOut(revoked=revoked)


@router.get("/share/{token}", response_class=HTMLResponse)
def get_shared_page(token: str, conn: Connection = Depends(get_conn)) -> str:
    target = resolve_share_token(conn, token)
    if target is None:
        return render_unavailable_html()

    if target.target_type == "activity":
        return render_activity_share_html(conn, target.target_id)

    if target.target_type == "period":
        if target.target_id == "all":
            return render_period_share_html(conn, target.athlete_id, "all", None)
        period_type, _, period_start = target.target_id.partition(":")
        if period_type not in VALID_PERIOD_TYPES:
            return render_unavailable_html()
        return render_period_share_html(conn, target.athlete_id, period_type, period_start)

    return render_unavailable_html()
