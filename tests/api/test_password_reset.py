"""Tests for the forgotten-password flow: POST /auth/forgot-password and /auth/reset-password."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.api.dependencies import get_duckdb, get_engine
from perseverer.api.main import app
from perseverer.api.routers import auth as auth_router
from perseverer.auth.lockout import MAX_FAILED_ATTEMPTS, record_attempt
from perseverer.auth.passwords import hash_password
from perseverer.auth.tokens import create_session_token
from perseverer.config import Settings, get_settings
from perseverer.db.schema import athlete
from perseverer.db.seed import DEFAULT_ATHLETE_ID

JWT_SECRET = "test-jwt-secret"
SMTP: dict[str, Any] = {
    "smtp_host": "smtp.example.test",
    "smtp_username": "mailer",
    "smtp_password": "x",
    "smtp_from": "perseverer@example.test",
}


def _set_account(engine: Engine, *, email: str | None = "runner@example.test") -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == DEFAULT_ATHLETE_ID)
            .values(username="runner", password_hash=hash_password("old-password"), email=email)
        )
        conn.commit()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    outbox: list[dict[str, Any]] = []
    monkeypatch.setattr(auth_router, "send_email", lambda settings, **kwargs: outbox.append(kwargs))
    return outbox


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _client(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection, *, smtp: bool = True
) -> TestClient:
    settings = Settings(
        data_dir=tmp_path,
        api_key="irrelevant",
        jwt_secret=JWT_SECRET,
        public_base_url="https://perseverer.example.test",
        **(SMTP if smtp else {}),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_duckdb] = lambda: duckdb_con.cursor()
    return TestClient(app)


def _token_from(mail: dict[str, Any]) -> str:
    match = re.search(r"/reset-password\?token=([\w.-]+)", mail["text_body"])
    assert match is not None
    return match.group(1)


def _login(client: TestClient, password: str) -> int:
    r = client.post("/api/v1/auth/login", json={"username": "runner", "password": password})
    return int(r.status_code)


def _reset(client: TestClient, token: str, password: str = "brand-new-pass") -> int:
    r = client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": password})
    return int(r.status_code)


def test_reset_link_by_username_or_email_sets_a_new_password(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection, sent: list[Any]
) -> None:
    _set_account(engine)
    client = _client(tmp_path, engine, duckdb_con)
    for identifier in ("runner", "Runner@Example.test"):
        r = client.post("/api/v1/auth/forgot-password", json={"identifier": identifier})
        assert (r.status_code, r.json()) == (200, {"email_configured": True})
    assert [m["to"] for m in sent] == ["runner@example.test"] * 2
    assert "https://perseverer.example.test/reset-password?token=" in sent[0]["text_body"]

    assert _reset(client, _token_from(sent[-1])) == 200
    assert _login(client, "brand-new-pass") == 200
    assert _login(client, "old-password") == 401


def test_a_link_works_once_and_older_links_die_with_it(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection, sent: list[Any]
) -> None:
    _set_account(engine)
    client = _client(tmp_path, engine, duckdb_con)
    client.post("/api/v1/auth/forgot-password", json={"identifier": "runner"})
    client.post("/api/v1/auth/forgot-password", json={"identifier": "runner"})
    first, second = _token_from(sent[0]), _token_from(sent[1])
    assert _reset(client, second) == 200
    assert _reset(client, second, "another-pass-1") == 400
    assert _reset(client, first, "another-pass-1") == 400


def test_unknown_account_gets_the_same_answer_and_no_email(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection, sent: list[Any]
) -> None:
    _set_account(engine, email=None)  # no email address on file: nothing to send to
    client = _client(tmp_path, engine, duckdb_con)
    for identifier in ("nobody", "runner"):
        r = client.post("/api/v1/auth/forgot-password", json={"identifier": identifier})
        assert (r.status_code, r.json()) == (200, {"email_configured": True})
    assert sent == []


def test_without_smtp_the_page_is_told_email_is_not_set_up(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection, sent: list[Any]
) -> None:
    _set_account(engine)
    client = _client(tmp_path, engine, duckdb_con, smtp=False)
    r = client.post("/api/v1/auth/forgot-password", json={"identifier": "runner"})
    assert r.json() == {"email_configured": False}
    assert sent == []


def test_requests_are_throttled_per_account(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection, sent: list[Any]
) -> None:
    _set_account(engine)
    client = _client(tmp_path, engine, duckdb_con)
    for _ in range(MAX_FAILED_ATTEMPTS + 3):
        r = client.post("/api/v1/auth/forgot-password", json={"identifier": "runner"})
        assert r.status_code == 200
    assert len(sent) == MAX_FAILED_ATTEMPTS


def test_rejects_bad_tokens_and_short_passwords(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection, sent: list[Any]
) -> None:
    _set_account(engine)
    client = _client(tmp_path, engine, duckdb_con)
    session, _ = create_session_token(DEFAULT_ATHLETE_ID, JWT_SECRET, 1)
    assert _reset(client, "garbage") == 400
    assert _reset(client, session) == 400  # a login token is not a reset token
    client.post("/api/v1/auth/forgot-password", json={"identifier": "runner"})
    assert _reset(client, _token_from(sent[0]), "short") == 422


def test_a_reset_lifts_a_lockout_from_earlier_wrong_guesses(
    tmp_path: Path, engine: Engine, duckdb_con: duckdb.DuckDBPyConnection, sent: list[Any]
) -> None:
    _set_account(engine)
    with engine.connect() as conn:
        for _ in range(MAX_FAILED_ATTEMPTS):
            record_attempt(conn, "runner", success=False)
        conn.commit()
    client = _client(tmp_path, engine, duckdb_con)
    client.post("/api/v1/auth/forgot-password", json={"identifier": "runner"})
    assert _reset(client, _token_from(sent[0])) == 200
    assert _login(client, "brand-new-pass") == 200
