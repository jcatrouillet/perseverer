"""End-to-end test of the MCP OAuth 2.1 flow (auth/oauth.py, api/routers/oauth.py,
docs/ARCHITECTURE.md)
exactly as a remote client like claude.ai drives it: discovery, dynamic client
registration, PKCE authorization, the login page, token exchange, an authenticated MCP call,
single-use codes, refresh rotation and revocation -- plus the header-key path Claude Code uses.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
from collections.abc import Generator
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from perseverer.api.main import app
from perseverer.auth.oauth import oauth_provider
from perseverer.auth.passwords import hash_password
from perseverer.config import get_settings
from perseverer.db.schema import athlete
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from tests.api.conftest import TEST_API_KEY, TEST_JWT_SECRET

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> Generator[None, None, None]:
    monkeypatch.setenv("PERSEVERER_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("PERSEVERER_JWT_SECRET", TEST_JWT_SECRET)
    get_settings.cache_clear()
    monkeypatch.setattr(oauth_provider, "_engine_factory", lambda: engine)
    yield
    get_settings.cache_clear()


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _set_password(engine: Engine, athlete_id: str, username: str, password: str) -> None:
    with engine.connect() as conn:
        conn.execute(
            athlete.update()
            .where(athlete.c.id == athlete_id)
            .values(username=username, password_hash=hash_password(password))
        )
        conn.commit()


def _authorize(c: TestClient, client_id: str, verifier: str) -> str:
    r = c.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
            "state": "xyz",
            "scope": "mcp",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert r.headers["location"].startswith("/oauth/login?req=")
    return str(r.headers["location"])


def test_full_oauth_flow(client: TestClient, engine: Engine) -> None:
    _set_password(engine, DEFAULT_ATHLETE_ID, "jerome", "correct-horse")

    # Second athlete: valid credentials, but the MCP connector is single-athlete.
    with engine.connect() as conn:
        conn.execute(
            athlete.insert().values(
                id="second",
                display_name="Second",
                timezone="UTC",
                unit_preference="metric",
                created_at=dt.datetime.now(dt.UTC),
                username="other",
                password_hash=hash_password("pw2"),
            )
        )
        conn.commit()

    with TestClient(app) as c:  # enters the app lifespan, which runs the MCP session manager
        # --- discovery ---
        meta = c.get("/.well-known/oauth-authorization-server")
        assert meta.status_code == 200
        assert meta.json()["registration_endpoint"].endswith("/register")
        assert c.get("/.well-known/oauth-protected-resource/mcp").status_code == 200

        # --- an unauthenticated MCP call is told where to authenticate ---
        r = c.post("/mcp", json=_INIT, headers=_MCP_HEADERS)
        assert r.status_code == 401
        assert "resource_metadata" in r.headers["www-authenticate"]

        # --- dynamic client registration (public client + PKCE, as claude.ai does) ---
        reg = c.post(
            "/register",
            json={
                "client_name": "Claude",
                "redirect_uris": [REDIRECT],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "mcp",
            },
        )
        assert reg.status_code == 201, reg.text
        client_id = reg.json()["client_id"]

        verifier = "v" * 64
        login_url = _authorize(c, client_id, verifier)
        req = parse_qs(urlparse(login_url).query)["req"][0]
        assert c.get(login_url).status_code == 200

        # --- the login page rejects bad credentials and never issues a code ---
        bad = c.post(
            "/oauth/login",
            data={"req": req, "username": "jerome", "password": "wrong"},
            follow_redirects=False,
        )
        assert bad.status_code == 401
        assert "location" not in bad.headers

        # --- a non-primary athlete is refused even with valid credentials ---
        other = c.post(
            "/oauth/login",
            data={"req": req, "username": "other", "password": "pw2"},
            follow_redirects=False,
        )
        assert other.status_code == 403

        # --- a tampered request token is rejected ---
        forged = c.post(
            "/oauth/login",
            data={"req": req + "x", "username": "jerome", "password": "correct-horse"},
            follow_redirects=False,
        )
        assert forged.status_code == 400

        ok = c.post(
            "/oauth/login",
            data={"req": req, "username": "jerome", "password": "correct-horse"},
            follow_redirects=False,
        )
        assert ok.status_code == 303
        redirect = urlparse(ok.headers["location"])
        assert f"{redirect.scheme}://{redirect.netloc}{redirect.path}" == REDIRECT
        query = parse_qs(redirect.query)
        assert query["state"] == ["xyz"]
        code = query["code"][0]

        token_body = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": REDIRECT,
        }
        # A wrong PKCE verifier is refused.
        assert c.post("/token", data={**token_body, "code_verifier": "w" * 64}).status_code == 400

        tokens = c.post("/token", data=token_body)
        assert tokens.status_code == 200, tokens.text
        access, refresh = tokens.json()["access_token"], tokens.json()["refresh_token"]

        # --- codes are single-use ---
        assert c.post("/token", data=token_body).status_code == 400

        # --- the access token works against /mcp; so does the shared API key header ---
        bearer = {**_MCP_HEADERS, "Authorization": f"Bearer {access}"}
        assert c.post("/mcp", json=_INIT, headers=bearer).status_code == 200
        keyed = {**_MCP_HEADERS, "X-API-Key": TEST_API_KEY}
        assert c.post("/mcp", json=_INIT, headers=keyed).status_code == 200
        # Stateless: a tool listing needs no prior `initialize` and no session id, so it cannot
        # fail by landing on a different worker than the one that handled the handshake.
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        stateless = c.post("/mcp", json=listing, headers=bearer)
        assert stateless.status_code == 200, stateless.text
        assert "create_planned_workout" in stateless.text
        wrong_key = {**_MCP_HEADERS, "X-API-Key": "nope"}
        assert c.post("/mcp", json=_INIT, headers=wrong_key).status_code == 401
        garbage = {**_MCP_HEADERS, "Authorization": "Bearer not-a-token"}
        assert c.post("/mcp", json=_INIT, headers=garbage).status_code == 401

        # --- refresh rotates: the old pair stops working ---
        refresh_body = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        }
        refreshed = c.post("/token", data=refresh_body)
        assert refreshed.status_code == 200, refreshed.text
        new_access = refreshed.json()["access_token"]
        assert c.post("/mcp", json=_INIT, headers=bearer).status_code == 401
        assert c.post("/token", data=refresh_body).status_code == 400

        # --- revocation ---
        new_bearer = {**_MCP_HEADERS, "Authorization": f"Bearer {new_access}"}
        assert c.post("/mcp", json=_INIT, headers=new_bearer).status_code == 200
        # The SDK's revocation request model requires a `client_secret` field even for a public
        # (auth method "none") client, so an empty one is what such a client has to send.
        revoke = c.post(
            "/revoke", data={"token": new_access, "client_id": client_id, "client_secret": ""}
        )
        assert revoke.status_code == 200, revoke.text
        assert c.post("/mcp", json=_INIT, headers=new_bearer).status_code == 401


def test_oauth_endpoints_do_not_widen_the_404_fallthrough(client: TestClient) -> None:
    """Only the OAuth endpoints and /mcp reach the MCP sub-app -- any other unmatched path is
    still a plain 404 (see test_mcp_mount_fallthrough.py's own incident note)."""
    assert client.get("/authorizex").status_code == 404
    assert client.get("/.well-known/security.txt").status_code == 404


def test_authorize_rejects_an_unregistered_client(client: TestClient) -> None:
    r = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "never-registered",
            "redirect_uri": REDIRECT,
            "code_challenge": _challenge("v" * 64),
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_login_page_rejects_a_garbled_request(client: TestClient) -> None:
    assert client.get("/oauth/login?req=garbage").status_code == 400
