"""FastAPI dependency injection: DB engine/connection, DuckDB connection, auth.

`get_engine`/`get_duckdb` are lazy per-worker-process singletons cached on `app.state`,
deliberately not created in a `lifespan=` hook -- that's what lets tests swap them via
`app.dependency_overrides` with no ordering dependency on `get_settings()`'s `@lru_cache` or
`TestClient` construction order. See tests/api/conftest.py and
docs/adr/0006-phase-3-read-api-and-rollups.md decision 4.
"""

from __future__ import annotations

import secrets
from collections.abc import Generator
from typing import Annotated

import duckdb
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import Connection, Engine, select

from sporthealth.api.duckdb_conn import make_duckdb_connection
from sporthealth.auth.api_keys import hash_api_key
from sporthealth.auth.tokens import InvalidSessionToken, verify_session_token
from sporthealth.config import Settings, get_settings
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import athlete
from sporthealth.db.seed import DEFAULT_ATHLETE_ID

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


def require_api_key(
    settings: SettingsDep,
    conn: Annotated[Connection, Depends(get_conn)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Resolves the authenticated athlete_id from any of three credentials (Phase 5, ADR
    0008): (a) X-API-Key matching the legacy process-wide SPORTHEALTH_API_KEY -> resolves to
    DEFAULT_ATHLETE_ID, so existing scripts and the Phase 4 MCP server keep working unchanged;
    (b) X-API-Key whose hash matches a per-athlete api_key_hash; (c) Authorization: Bearer
    <jwt> issued by POST /auth/login. Fails closed: no credential presented AND neither
    SPORTHEALTH_API_KEY nor SPORTHEALTH_JWT_SECRET configured -> 503, never silently open. See
    ADR 0006 decision 6 (the original single-key contract this extends) and ADR 0008.
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
