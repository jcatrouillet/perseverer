"""Tests for POST /auth/login (Phase 5, ADR 0008)."""

from pathlib import Path

import duckdb
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.api.dependencies import get_duckdb, get_engine
from perseverer.api.main import app
from perseverer.auth.passwords import hash_password
from perseverer.auth.tokens import verify_session_token
from perseverer.config import Settings, get_settings
from perseverer.db.schema import athlete
from perseverer.db.seed import DEFAULT_ATHLETE_ID

JWT_SECRET = "test-jwt-secret"


def _set_password(engine: Engine, username: str, password: str) -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(username=username, password_hash=hash_password(password))
        )
        conn.commit()


def _client_with_jwt_secret(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection, jwt_secret: str | None
) -> TestClient:
    settings = Settings(data_dir=tmp_path, api_key="irrelevant", jwt_secret=jwt_secret)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    return TestClient(app)


def test_login_success_returns_verifiable_jwt(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    _set_password(engine, "jerome", "hunter2")
    client = _client_with_jwt_secret(tmp_path, engine, duckdb_con, JWT_SECRET)
    try:
        r = client.post("/api/v1/auth/login", json={"username": "jerome", "password": "hunter2"})
        assert r.status_code == 200
        body = r.json()
        assert verify_session_token(body["access_token"], JWT_SECRET) == DEFAULT_ATHLETE_ID
        assert "expires_at" in body
    finally:
        app.dependency_overrides.clear()


def test_login_wrong_password_401(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    _set_password(engine, "jerome", "hunter2")
    client = _client_with_jwt_secret(tmp_path, engine, duckdb_con, JWT_SECRET)
    try:
        r = client.post("/api/v1/auth/login", json={"username": "jerome", "password": "wrong"})
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_login_unknown_username_401(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    client = _client_with_jwt_secret(tmp_path, engine, duckdb_con, JWT_SECRET)
    try:
        r = client.post(
            "/api/v1/auth/login", json={"username": "nobody", "password": "whatever"}
        )
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_login_jwt_secret_unset_503(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    _set_password(engine, "jerome", "hunter2")
    client = _client_with_jwt_secret(tmp_path, engine, duckdb_con, None)
    try:
        r = client.post("/api/v1/auth/login", json={"username": "jerome", "password": "hunter2"})
        assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()
