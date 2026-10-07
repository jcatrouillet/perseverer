"""Tests for require_api_key: fail-closed on an unset key, 401 on wrong/missing, /healthz and
/version stay open. docs/ARCHITECTURE.md.

require_api_key resolves to resolve *which* athlete authenticated, from any
of three credentials -- the additional tests below cover per-athlete API keys, JWT bearer
tokens, and that resolution actually scopes queries (not just gates access).
"""

import datetime as dt
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.api.dependencies import get_duckdb, get_engine
from perseverer.api.main import app
from perseverer.auth.api_keys import generate_api_key, hash_api_key
from perseverer.auth.tokens import create_session_token
from perseverer.config import Settings, get_settings
from perseverer.db.schema import athlete
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from tests.api.conftest import seed_activity


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


def test_unset_api_key_and_jwt_secret_fails_closed_503_with_no_credential(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    """The 503 case: it now only fires when NO credential is
    presented at all and neither the legacy shared key nor JWT signing is configured -- once
    per-athlete API keys exist as an independent mechanism, a *presented* X-API-Key that
    simply doesn't match anything is correctly a 401 (see test below), not a 503, even if the
    legacy shared key happens to be unset.
    """
    settings = Settings(data_dir=tmp_path, api_key=None, jwt_secret=None)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    try:
        no_key_client = TestClient(app)
        r = no_key_client.get("/api/v1/activities")
        assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_unmatched_key_with_legacy_key_unset_is_401_not_503(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    settings = Settings(data_dir=tmp_path, api_key=None, jwt_secret="test-jwt-secret")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    try:
        no_key_client = TestClient(app)
        r = no_key_client.get("/api/v1/activities", headers={"X-API-Key": "anything"})
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


SECOND_ATHLETE_ID = "athlete2"


def _seed_second_athlete(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id=SECOND_ATHLETE_ID,
                display_name="Second",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
            )
        )
        conn.commit()


def test_per_athlete_api_key_resolves_and_scopes_to_that_athlete(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    _seed_second_athlete(engine)
    raw_key = generate_api_key()
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == SECOND_ATHLETE_ID)
            .values(api_key_hash=hash_api_key(raw_key))
        )
        conn.commit()
        seed_activity(conn, activity_id="default-act")

    settings = Settings(data_dir=tmp_path, api_key="legacy-key", jwt_secret="test-jwt-secret")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    try:
        c = TestClient(app)
        r = c.get("/api/v1/activities", headers={"X-API-Key": raw_key})
        assert r.status_code == 200
        # The second athlete has no activities of their own -- the default athlete's seeded
        # activity must NOT be visible, proving the resolved key actually scopes the query.
        assert r.json()["total"] == 0

        r_default = c.get("/api/v1/activities", headers={"X-API-Key": "legacy-key"})
        assert r_default.status_code == 200
        assert r_default.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()


def test_per_athlete_api_key_cannot_modify_another_athletes_data(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    """Read-scoping (above) isn't the whole story -- a key must also be unable to *write* to a
    row it can't see, addressed by id rather than by an already-scoped list query. Every
    PUT/DELETE-by-id route in this codebase re-checks `athlete_id` in its own WHERE clause (see
    e.g. notes.py::update_note/delete_note, planned_races.py::put/delete), so a mismatched
    athlete_id 404s instead of touching the row -- this test exercises that specifically via
    /notes, but the same WHERE-clause pattern is what protects every other by-id route too.
    """
    _seed_second_athlete(engine)
    raw_key = generate_api_key()
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == SECOND_ATHLETE_ID)
            .values(api_key_hash=hash_api_key(raw_key))
        )
        conn.commit()

    settings = Settings(data_dir=tmp_path, api_key="legacy-key", jwt_secret="test-jwt-secret")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    try:
        c = TestClient(app)
        created = c.post(
            "/api/v1/notes",
            json={
                "entity_type": "day",
                "entity_id": "2026-01-01",
                "body": "default athlete's note",
            },
            headers={"X-API-Key": "legacy-key"},
        ).json()

        # The second athlete's own valid key can't edit or delete a note it doesn't own, even
        # though it knows the note's real id.
        r_put = c.put(
            f"/api/v1/notes/{created['id']}",
            json={"body": "hijacked"},
            headers={"X-API-Key": raw_key},
        )
        assert r_put.status_code == 404
        r_delete = c.delete(f"/api/v1/notes/{created['id']}", headers={"X-API-Key": raw_key})
        assert r_delete.status_code == 404

        # The note is untouched.
        listed = c.get(
            "/api/v1/notes?entity_type=day&entity_id=2026-01-01",
            headers={"X-API-Key": "legacy-key"},
        ).json()
        assert len(listed) == 1
        assert listed[0]["body"] == "default athlete's note"
    finally:
        app.dependency_overrides.clear()


def test_bearer_jwt_resolves_athlete(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    with engine.connect() as conn:
        seed_activity(conn, activity_id="default-act")

    settings = Settings(data_dir=tmp_path, api_key=None, jwt_secret="test-jwt-secret")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    try:
        token, _ = create_session_token(DEFAULT_ATHLETE_ID, "test-jwt-secret", expiry_days=30)
        c = TestClient(app)
        r = c.get("/api/v1/activities", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()


def test_expired_bearer_jwt_401(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    settings = Settings(data_dir=tmp_path, api_key=None, jwt_secret="test-jwt-secret")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    try:
        token, _ = create_session_token(DEFAULT_ATHLETE_ID, "test-jwt-secret", expiry_days=-1)
        c = TestClient(app)
        r = c.get("/api/v1/activities", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_bearer_jwt_with_jwt_secret_unset_503(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection
) -> None:
    settings = Settings(data_dir=tmp_path, api_key=None, jwt_secret=None)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    try:
        c = TestClient(app)
        r = c.get("/api/v1/activities", headers={"Authorization": "Bearer whatever"})
        assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()
