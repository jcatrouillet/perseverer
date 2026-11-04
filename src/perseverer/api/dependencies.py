"""FastAPI dependency injection: DB engine/connection, DuckDB connection, auth.

`get_engine`/`get_duckdb` are lazy per-worker-process singletons cached on `app.state`,
deliberately not created in a `lifespan=` hook -- that's what lets tests swap them via
`app.dependency_overrides` with no ordering dependency on `get_settings()`'s `@lru_cache` or
`TestClient` construction order. See tests/api/conftest.py and
docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import secrets
from collections.abc import Generator
from typing import Annotated

import duckdb
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import Connection, Engine, select

from perseverer.api.duckdb_conn import make_duckdb_connection
from perseverer.auth.api_keys import hash_api_key
from perseverer.auth.tokens import InvalidSessionToken, verify_session_token
from perseverer.config import Settings, get_settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import athlete, coach_link
from perseverer.db.seed import DEFAULT_ATHLETE_ID

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_engine(request: Request, settings: SettingsDep) -> Engine:
    if not hasattr(request.app.state, "engine"):
        request.app.state.engine = make_engine(settings.db_path)
    engine: Engine = request.app.state.engine
    return engine


def get_conn(engine: Annotated[Engine, Depends(get_engine)]) -> Generator[Connection, None, None]:
    with engine.connect() as conn:
        yield conn


def get_duckdb(request: Request, settings: SettingsDep) -> duckdb.DuckDBPyConnection:
    if not hasattr(request.app.state, "duckdb_singleton"):
        request.app.state.duckdb_singleton = make_duckdb_connection(
            settings.db_path, settings.duckdb_extension_dir
        )
    singleton: duckdb.DuckDBPyConnection = request.app.state.duckdb_singleton
    # .cursor() shares the same attach but gives this request its own handle -- verified safe
    # for concurrent use, see duckdb_conn.py's module docstring.
    return singleton.cursor()


def _authenticate(
    settings: Settings,
    conn: Connection,
    x_api_key: str | None,
    authorization: str | None,
) -> str:
    """Resolves the authenticated athlete_id from any of three credentials:
    (a) X-API-Key matching the legacy process-wide PERSEVERER_API_KEY -> resolves to
    DEFAULT_ATHLETE_ID, so existing scripts and the MCP server keep working unchanged;
    (b) X-API-Key whose hash matches a per-athlete api_key_hash; (c) Authorization: Bearer
    <jwt> issued by POST /auth/login. Fails closed: no credential presented AND neither
    PERSEVERER_API_KEY nor PERSEVERER_JWT_SECRET configured -> 503, never silently open. See
    docs/ARCHITECTURE.md.
    """
    if x_api_key:
        if settings.api_key and secrets.compare_digest(x_api_key, settings.api_key):
            return DEFAULT_ATHLETE_ID
        matched = conn.execute(
            select(athlete.c.id).where(athlete.c.api_key_hash == hash_api_key(x_api_key))
        ).scalar_one_or_none()
        if matched is not None:
            return str(matched)
        raise HTTPException(status_code=401, detail="invalid API key")

    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="invalid Authorization header")
        if not settings.jwt_secret:
            raise HTTPException(status_code=503, detail="JWT signing not configured")
        try:
            return verify_session_token(token, settings.jwt_secret)
        except InvalidSessionToken as exc:
            raise HTTPException(status_code=401, detail="invalid or expired session") from exc

    if not settings.api_key and not settings.jwt_secret:
        raise HTTPException(status_code=503, detail="authentication not configured")
    raise HTTPException(status_code=401, detail="missing credentials")


#: What a coach may change while viewing a coachee's data: comments (notes) and planned workouts.
#: Every read is allowed; anything else is refused, so a coach can never touch the coachee's
#: credentials, settings, sharing or sync.
COACH_WRITE_PREFIXES = ("/api/v1/notes", "/api/v1/planned-workouts")
#: Planned-workout actions that are the athlete's own: sending to their Garmin account, and marking
#: a workout done.
COACH_REFUSED_SUFFIXES = ("/push", "/complete", "/uncomplete")
#: Reads that would hand the coachee's standing secrets to the coach.
COACH_BLOCKED_READ_PREFIXES = ("/api/v1/settings/api-key", "/api/v1/settings/calendar-feed")


def coach_may(method: str, path: str) -> bool:
    """Whether a coach viewing a coachee's data may make this request."""
    if method in ("GET", "HEAD", "OPTIONS"):
        return not path.startswith(COACH_BLOCKED_READ_PREFIXES)
    return path.startswith(COACH_WRITE_PREFIXES) and not path.endswith(COACH_REFUSED_SUFFIXES)


def require_api_key(
    request: Request,
    settings: SettingsDep,
    conn: Annotated[Connection, Depends(get_conn)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_act_as: Annotated[str | None, Header(alias="X-Act-As")] = None,
) -> str:
    """The athlete whose data this request reads and writes.

    Normally the authenticated athlete. With `X-Act-As: <athlete id>` it is that athlete instead,
    provided they chose the caller as their coach (`coach_link`) and the request is one a coach may
    make (`coach_may`). The authenticated athlete stays available as `request.state.actor_id`
    (`get_actor_id`), so a comment or an activity can record who really wrote it.
    """
    actor_id = _authenticate(settings, conn, x_api_key, authorization)
    request.state.actor_id = actor_id
    if not x_act_as or x_act_as == actor_id:
        return actor_id
    linked = conn.execute(
        select(coach_link.c.athlete_id).where(
            coach_link.c.athlete_id == x_act_as, coach_link.c.coach_athlete_id == actor_id
        )
    ).scalar_one_or_none()
    if linked is None:
        raise HTTPException(status_code=403, detail="you are not this athlete's coach")
    if not coach_may(request.method, request.url.path):
        raise HTTPException(
            status_code=403,
            detail="a coach can only plan workouts and edit comments for an athlete",
        )
    return x_act_as


def get_actor_id(request: Request, athlete_id: str) -> str:
    """Who really made this request: the athlete itself, or the coach acting for them."""
    actor = getattr(request.state, "actor_id", None)
    return actor if isinstance(actor, str) else athlete_id
