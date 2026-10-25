"""POST/GET/DELETE /posters -- year posters (posters.py) rendered as background jobs.

A request creates a `poster` row (queued) and returns at once; the render runs after the response
in this process (like the Settings page's other long operations), so the page polls the row until
it is done and then downloads the PNG.  The row is the job and its history; the file lives under
<data_dir>/posters and can always be regenerated.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import Connection, Engine, func, select
from ulid import ULID

from perseverer.api.dependencies import SettingsDep, get_conn, get_engine, require_api_key
from perseverer.api.schemas.posters import (
    HeatmapDefaultsOut,
    PosterIn,
    PosterOut,
    PosterSportOut,
)
from perseverer.db.schema import activity, athlete, poster, route_geom
from perseverer.posters import (
    IMPERIAL,
    METRIC,
    ROUTE_KINDS,
    HeatmapView,
    NoPosterData,
    PosterKind,
    default_heatmap_view,
    load_activities,
    poster_filename,
    render_poster,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# A job that has not finished this long after it was asked for was lost with a restart.
STALE_AFTER = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage


def _poster_dir(data_dir: Path) -> Path:
    return data_dir / "posters"


def _out(row: Any) -> PosterOut:
    params = json.loads(row.params)
    return PosterOut(
        id=row.id,
        kind=row.kind,
        year=row.year,
        sports=params.get("sports", []),
        include_virtual=bool(params.get("include_virtual", False)),
        center_lat=params.get("center_lat"),
        center_lon=params.get("center_lon"),
        radius_km=params.get("radius_km"),
        status=row.status,
        error=row.error,
        filename=row.filename,
        size_bytes=row.size_bytes,
        activity_count=row.activity_count,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def _expire_stale(conn: Connection, athlete_id: str) -> None:
    """A job still queued or running long after it was asked for is one a restart cut short."""
    conn.execute(
        poster.update()
        .where(
            poster.c.athlete_id == athlete_id,
            poster.c.status.in_(("queued", "running")),
            poster.c.created_at < _now() - STALE_AFTER,
        )
        .values(
            status="failed",
            error="Interrupted: the server restarted before this poster was finished.",
            finished_at=_now(),
        )
    )
    conn.commit()


def run_poster_job(engine: Engine, data_dir: Path, poster_id: str) -> None:
    """Renders one queued poster.  Reads, renders and writes in separate short steps, so no
    database transaction is open while the picture is drawn."""
    try:
        with engine.connect() as conn:
            row = conn.execute(select(poster).where(poster.c.id == poster_id)).one()
            conn.execute(
                poster.update()
                .where(poster.c.id == poster_id)
                .values(status="running", started_at=_now())
            )
            conn.commit()
            params = json.loads(row.params)
            kind: PosterKind = row.kind
            owner = conn.execute(
                select(athlete.c.display_name, athlete.c.unit_preference).where(
                    athlete.c.id == row.athlete_id
                )
            ).one()
            acts = load_activities(
                conn,
                row.athlete_id,
                year=row.year,
                sports=params.get("sports", []),
                include_virtual=bool(params.get("include_virtual", False)),
                with_routes=kind in ROUTE_KINDS,
            )
            view: HeatmapView | None = None
            if kind == "heatmap":
                default = default_heatmap_view(conn, row.athlete_id, acts)
                lat, lon = params.get("center_lat"), params.get("center_lon")
                radius = params.get("radius_km")
                if lat is not None and lon is not None:
                    from perseverer.posters import radius_for_share

                    starts = [a.start for a in acts if a.start is not None]
                    view = HeatmapView(
                        center_lat=lat,
                        center_lon=lon,
                        radius_km=radius or radius_for_share((lat, lon), starts),
                        source="chosen",
                    )
                elif default is not None:
                    view = HeatmapView(
                        default.center_lat,
                        default.center_lon,
                        radius or default.radius_km,
                        default.source,
                    )
            conn.commit()  # end the read transaction before drawing

        image = render_poster(
            kind,
            acts,
            year=row.year,
            name=owner.display_name or "",
            units=IMPERIAL if owner.unit_preference == "imperial" else METRIC,
            view=view,
        )
        directory = _poster_dir(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{poster_id}.png"
        image.save(path, format="PNG", optimize=False)
        finished = _now()
        filename = poster_filename(kind, row.year, params.get("sports", []), finished, poster_id)
        with engine.connect() as conn:
            conn.execute(
                poster.update()
                .where(poster.c.id == poster_id)
                .values(
                    status="done",
                    filename=filename,
                    size_bytes=path.stat().st_size,
                    activity_count=len(acts),
                    finished_at=finished,
                )
            )
            conn.commit()
    except NoPosterData as e:
        _fail(engine, poster_id, str(e))
    except Exception:
        logger.exception("poster %s failed", poster_id)
        _fail(engine, poster_id, "The poster could not be drawn.")


def _fail(engine: Engine, poster_id: str, message: str) -> None:
    with engine.connect() as conn:
        conn.execute(
            poster.update()
            .where(poster.c.id == poster_id)
            .values(status="failed", error=message, finished_at=_now())
        )
        conn.commit()


@router.post("/posters", status_code=202)
def create_poster(
    payload: PosterIn,
    background: BackgroundTasks,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    engine: Engine = Depends(get_engine),
    conn: Connection = Depends(get_conn),
) -> PosterOut:
    """Queues a poster and returns it (status `queued`); poll `GET /posters/{id}`."""
    if (payload.center_lat is None) != (payload.center_lon is None):
        raise HTTPException(status_code=422, detail="center_lat and center_lon go together")
    poster_id = str(ULID())
    params = {
        "sports": sorted(set(payload.sports)),
        "include_virtual": payload.include_virtual,
        "center_lat": payload.center_lat,
        "center_lon": payload.center_lon,
        "radius_km": payload.radius_km,
    }
    conn.execute(
        poster.insert().values(
            id=poster_id,
            athlete_id=athlete_id,
            kind=payload.kind,
            year=payload.year,
            params=json.dumps(params),
            status="queued",
            created_at=_now(),
        )
    )
    conn.commit()
    background.add_task(run_poster_job, engine, settings.data_dir, poster_id)
    return _out(conn.execute(select(poster).where(poster.c.id == poster_id)).one())


@router.get("/posters")
def list_posters(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    year: int | None = Query(default=None),
) -> list[PosterOut]:
    """The athlete's posters, newest first; `year` keeps just that year's."""
    _expire_stale(conn, athlete_id)
    query = select(poster).where(poster.c.athlete_id == athlete_id)
    if year is not None:
        query = query.where(poster.c.year == year)
    return [_out(r) for r in conn.execute(query.order_by(poster.c.created_at.desc())).fetchall()]


@router.get("/posters/sports")
def poster_sports(
    athlete_id: Annotated[str, Depends(require_api_key)],
    year: Annotated[int, Query(ge=1990, le=2100)],
    include_virtual: bool = Query(default=False),
    conn: Connection = Depends(get_conn),
) -> list[PosterSportOut]:
    """The sports the year has, with how many activities each and how many of those have a GPS
    trace -- what the poster page offers to choose from."""
    query = (
        select(
            activity.c.sport,
            func.count().label("n"),
            func.count(route_geom.c.encoded_polyline).label("routes"),
        )
        .select_from(activity.outerjoin(route_geom, route_geom.c.activity_id == activity.c.id))
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            activity.c.local_date >= f"{year}-01-01",
            activity.c.local_date <= f"{year}-12-31",
        )
        .group_by(activity.c.sport)
        .order_by(func.count().desc())
    )
    if not include_virtual:
        query = query.where(activity.c.is_virtual.is_(False))
    return [
        PosterSportOut(sport=r.sport, activities=r.n, with_route=r.routes)
        for r in conn.execute(query).fetchall()
    ]


@router.get("/posters/heatmap-defaults")
def heatmap_defaults(
    athlete_id: Annotated[str, Depends(require_api_key)],
    year: Annotated[int, Query(ge=1990, le=2100)],
    sports: Annotated[list[str] | None, Query()] = None,
    include_virtual: bool = Query(default=False),
    conn: Connection = Depends(get_conn),
) -> HeatmapDefaultsOut:
    """Where the heatmap is centred and how far it reaches by default: the home address (Settings
    -> Commute), else the profile's home point, and the smallest radius that holds at least half of
    the selected activities."""
    acts = load_activities(
        conn,
        athlete_id,
        year=year,
        sports=sports or [],
        include_virtual=include_virtual,
        with_routes=True,
    )
    view = default_heatmap_view(conn, athlete_id, acts)
    if view is None:
        return HeatmapDefaultsOut(available=False, activities=len(acts))
    return HeatmapDefaultsOut(
        available=True,
        center_lat=view.center_lat,
        center_lon=view.center_lon,
        radius_km=view.radius_km,
        source=view.source,
        activities=len(acts),
    )


def _get(conn: Connection, athlete_id: str, poster_id: str) -> Any:
    _expire_stale(conn, athlete_id)
    row = conn.execute(
        select(poster).where(poster.c.id == poster_id, poster.c.athlete_id == athlete_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="poster not found")
    return row


@router.get("/posters/{poster_id}")
def get_poster(
    poster_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> PosterOut:
    return _out(_get(conn, athlete_id, poster_id))


@router.get("/posters/{poster_id}/file")
def download_poster(
    poster_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
) -> FileResponse:
    row = _get(conn, athlete_id, poster_id)
    path = _poster_dir(settings.data_dir) / f"{row.id}.png"
    if row.status != "done" or not path.exists():
        raise HTTPException(status_code=409, detail="the poster is not ready")
    return FileResponse(path, media_type="image/png", filename=row.filename or f"{row.id}.png")


@router.delete("/posters/{poster_id}")
def delete_poster(
    poster_id: str,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: SettingsDep,
    conn: Connection = Depends(get_conn),
) -> None:
    row = _get(conn, athlete_id, poster_id)
    (_poster_dir(settings.data_dir) / f"{row.id}.png").unlink(missing_ok=True)
    conn.execute(poster.delete().where(poster.c.id == row.id))
    conn.commit()
