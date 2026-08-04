"""Tests for require_api_key: fail-closed on an unset key, 401 on wrong/missing, /healthz and
/version stay open. See docs/adr/0006-phase-3-read-api-and-rollups.md decision 6.
"""

from pathlib import Path

import duckdb
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from sporthealth.api.dependencies import get_duckdb, get_engine
from sporthealth.api.main import app
from sporthealth.config import Settings, get_settings


def test_missing_key_returns_401(client: TestClient) -> None:
    r = client.get("/api/v1/activities")
    assert r.status_code == 401


def test_wrong_key_returns_401(client: TestClient) -> None:
    r = client.get("/api/v1/activities", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_correct_key_returns_200(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get("/api/v1/activities", headers=auth_headers)
    assert r.status_code == 200


def test_healthz_and_version_stay_open_with_no_key(client: TestClient) -> None:
    assert client.get("/api/v1/healthz").status_code == 200
    assert client.get("/api/v1/version").status_code == 200


def test_unset_api_key_fails_closed_503(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    settings = Settings(data_dir=tmp_path, api_key=None)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    try:
        no_key_client = TestClient(app)
        r = no_key_client.get("/api/v1/activities", headers={"X-API-Key": "anything"})
        assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()
