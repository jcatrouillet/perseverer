"""GET /settings/kaya/status, POST /settings/kaya/login, POST /settings/kaya/sync -- the Settings
page's web counterparts of `sync auth kaya-login` / `sync import kaya`. Kaya itself is
never contacted: login and the import are patched at the router module."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.adapters import kaya
from perseverer.adapters.kaya_ingest import KayaImportSummary
from perseverer.api.routers import settings as settings_router
from perseverer.config import Settings
from perseverer.db.schema import ingest_run
from perseverer.db.seed import DEFAULT_ATHLETE_ID


def _fake_login(settings: Settings) -> Any:
    def login(email: str, password: str, tokenstore_dir: Path) -> None:
        assert (email, password) == ("me@example.com", "hunter2")
        kaya.save_tokens(
            tokenstore_dir, kaya.KayaTokens("t", "r", "1", dt.datetime.now(dt.UTC).isoformat())
        )

    return login


def test_status_login_then_status(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    before = client.get("/api/v1/settings/kaya/status", headers=auth_headers).json()
    assert before["session_present"] is False and before["last_sync_status"] is None

    monkeypatch.setattr(settings_router, "kaya_login", _fake_login(test_settings))
    r = client.post(
        "/api/v1/settings/kaya/login",
        json={"email": "me@example.com", "password": "hunter2"},
        headers=auth_headers,
    )
    assert r.status_code == 200 and r.json() == {"success": True}

    after = client.get("/api/v1/settings/kaya/status", headers=auth_headers).json()
    assert after["session_present"] is True and after["session_age_days"] == 0
    # The password is never stored: only the tokens file exists.
    tokens = test_settings.kaya_tokenstore_dir_for(DEFAULT_ATHLETE_ID) / kaya.TOKEN_FILENAME
    assert "hunter2" not in tokens.read_text()


def test_wrong_password_is_400_not_401_and_network_failure_is_502(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def invalid(*a: object, **kw: object) -> None:
        raise kaya.KayaInvalidCredentials("no")

    monkeypatch.setattr(settings_router, "kaya_login", invalid)
    body = {"email": "me@example.com", "password": "wrong"}
    assert (
        client.post("/api/v1/settings/kaya/login", json=body, headers=auth_headers).status_code
        == 400
    )

    def unreachable(*a: object, **kw: object) -> None:
        raise requests.ConnectionError("down")

    monkeypatch.setattr(settings_router, "kaya_login", unreachable)
    assert (
        client.post("/api/v1/settings/kaya/login", json=body, headers=auth_headers).status_code
        == 502
    )


def test_sync_needs_a_session_then_runs_the_import(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    assert client.post("/api/v1/settings/kaya/sync", headers=auth_headers).status_code == 400

    kaya.save_tokens(
        test_settings.kaya_tokenstore_dir_for(DEFAULT_ATHLETE_ID),
        kaya.KayaTokens("t", "r", "1", dt.datetime.now(dt.UTC).isoformat()),
    )
    calls: list[str] = []

    def fake_import(conn: Any, raw_dir: Any, *, athlete_id: str, tokenstore_dir: Any) -> Any:
        calls.append(athlete_id)
        return KayaImportSummary(pages=1, sessions=1, ascents=2, touched_dates=1)

    monkeypatch.setattr(settings_router, "import_kaya", fake_import)
    r = client.post("/api/v1/settings/kaya/sync", headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"triggered": True}
    assert calls == [DEFAULT_ATHLETE_ID]


def test_a_failed_background_import_does_not_error_the_request(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    kaya.save_tokens(
        test_settings.kaya_tokenstore_dir_for(DEFAULT_ATHLETE_ID),
        kaya.KayaTokens("t", "r", "1", dt.datetime.now(dt.UTC).isoformat()),
    )

    def boom(*a: object, **kw: object) -> None:
        raise RuntimeError("kaya exploded")

    monkeypatch.setattr(settings_router, "import_kaya", boom)
    assert client.post("/api/v1/settings/kaya/sync", headers=auth_headers).status_code == 200


def test_jobs_latest_and_status_report_the_kaya_run(
    client: TestClient, auth_headers: dict[str, str], engine: Engine
) -> None:
    assert (
        client.get("/api/v1/settings/jobs/latest?source=kaya", headers=auth_headers).json() is None
    )
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        conn.execute(
            ingest_run.insert().values(
                athlete_id=DEFAULT_ATHLETE_ID,
                source="kaya",
                started_at=now,
                finished_at=now,
                status="failed",
                items_seen=0,
                items_new=0,
                errors='[{"error": "Kaya session expired"}]',
            )
        )
        conn.commit()
    job = client.get("/api/v1/settings/jobs/latest?source=kaya", headers=auth_headers).json()
    assert job["status"] == "failed" and job["first_error"] == "Kaya session expired"
    status = client.get("/api/v1/settings/kaya/status", headers=auth_headers).json()
    assert status["last_sync_status"] == "failed"
    assert status["last_sync_error"] == "Kaya session expired"
