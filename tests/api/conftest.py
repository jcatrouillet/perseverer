"""Shared fixtures for API tests.

`app` (from api/main.py) is a module-level singleton shared across every test in the process,
and `get_engine`/`get_duckdb`'s real implementations cache their resources on `request.app.state`
-- which would persist across tests since it's the same `app` object. That's exactly why they're
lazy/DI-based rather than `lifespan`-created (see docs/adr/0006-phase-3-read-api-and-rollups.md
decision 4): `app.dependency_overrides` replaces them entirely, bypassing the app.state cache,
so each test gets its own tmp SQLite DB and DuckDB connection with no cross-test leakage.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Generator
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine

from sporthealth.api.dependencies import get_duckdb, get_engine
from sporthealth.api.duckdb_conn import make_duckdb_connection
from sporthealth.api.main import app
from sporthealth.config import Settings, get_settings
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity, athlete, metadata
from sporthealth.db.seed import DEFAULT_ATHLETE_ID

TEST_API_KEY = "test-api-key"


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    eng = make_engine(tmp_path / "db.sqlite")
    metadata.create_all(eng)
    with eng.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=DEFAULT_ATHLETE_ID,
                display_name="Test",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()
    return eng


@pytest.fixture
def duckdb_con(tmp_path: Path, engine: Engine) -> duckdb.DuckDBPyConnection:
    return make_duckdb_connection(tmp_path / "db.sqlite")


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, api_key=TEST_API_KEY)


@pytest.fixture
def client(
    test_settings: Settings, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_API_KEY}


def seed_activity(
    conn: Connection,
    *,
    activity_id: str = "act1",
    local_date: str = "2025-06-01",
    sport: str = "running",
    duration_s: float = 1800.0,
    distance_m: float = 5000.0,
    moving_duration_s: float | None = None,
) -> None:
    now = dt.datetime(2025, 6, 1, 10, 0, 0)
    conn.execute(
        activity.insert().values(
            id=activity_id,
            athlete_id=DEFAULT_ATHLETE_ID,
            start_time_utc=now,
            utc_offset_s=0,
            local_date=local_date,
            sport=sport,
            duration_s=duration_s,
            moving_duration_s=(
                moving_duration_s if moving_duration_s is not None else duration_s * 0.95
            ),
            distance_m=distance_m,
            elevation_gain_m=50.0,
            calories=300.0,
            primary_source="fit_folder",
            created_at=now,
            updated_at=now,
        )
    )
    conn.commit()
