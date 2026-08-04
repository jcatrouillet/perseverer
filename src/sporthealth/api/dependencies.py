"""FastAPI dependency injection: DB engine/connection, DuckDB connection, API-key auth.

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
from sqlalchemy import Connection, Engine

from sporthealth.api.duckdb_conn import make_duckdb_connection
from sporthealth.config import Settings, get_settings
from sporthealth.db.engine import make_engine

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
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Fails closed: an unset SPORTHEALTH_API_KEY means every protected route 503s, never
    silently opens up. See ADR 0006 decision 6."""
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="API key not configured")
    if not secrets.compare_digest(x_api_key or "", settings.api_key):
        raise HTTPException(status_code=401, detail="invalid or missing API key")
