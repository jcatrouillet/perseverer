"""Kaya (kayaclimb.com) authentication and token store -- the first slice of the Kaya adapter
(ADR 0016). Only login/refresh/status live here so far; session and ascent ingestion come next.

Kaya has no public API. This talks to the same private REST login the mobile/web app uses
(`POST /api/user/login` -> `{token, refresh_token, user: {id}}`), so treat every shape here as
subject to change without notice; a break is confined to this file.

Safety contract, mirroring `garmin_connect`: credentials are used in exactly one place,
`login_with_credentials`, invoked only by the human-initiated `sync auth kaya-login`. Only the
tokens are persisted (never the password), and `load_tokens`/`refresh_access_token` never fall
back to credentials -- a dead refresh token raises `KayaAuthRequired`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

KAYA_API_BASE = "https://kaya-beta.kayaclimb.com"
KAYA_APP_BASE = "https://kaya-app.kayaclimb.com"
TOKEN_FILENAME = "tokens.json"
REQUEST_TIMEOUT_S = 30


class KayaInvalidCredentials(Exception):
    """Kaya rejected the email/password."""


class KayaAuthRequired(Exception):
    """No usable stored session; a human must run `sync auth kaya-login`."""


@dataclass(frozen=True)
class KayaTokens:
    token: str
    refresh_token: str
    user_id: str
    saved_at: str  # ISO-8601 UTC


def _headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "origin": KAYA_APP_BASE,
        "referer": f"{KAYA_APP_BASE}/",
    }
    if token:
        headers["authorization"] = f"Bearer {token}"
    return headers


def _post(path: str, body: dict[str, Any], base_url: str) -> requests.Response:
    return requests.post(
        f"{base_url}{path}", json=body, headers=_headers(), timeout=REQUEST_TIMEOUT_S
    )


def save_tokens(tokenstore_dir: Path, tokens: KayaTokens) -> None:
    """Persists the Kaya access/refresh tokens -- never the password."""
    tokenstore_dir.mkdir(parents=True, exist_ok=True)
    path = tokenstore_dir / TOKEN_FILENAME
    path.write_text(json.dumps(asdict(tokens)), encoding="utf-8")
    with contextlib.suppress(OSError):  # best effort (Windows)
        os.chmod(path, 0o600)


def load_tokens(tokenstore_dir: Path) -> KayaTokens:
    """Loads the stored Kaya session; raises `KayaAuthRequired` when there is none (the import never
    falls back to credentials).
    """
    path = tokenstore_dir / TOKEN_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return KayaTokens(
            token=str(data["token"]),
            refresh_token=str(data["refresh_token"]),
            user_id=str(data["user_id"]),
            saved_at=str(data["saved_at"]),
        )
    except (OSError, ValueError, KeyError) as exc:
        raise KayaAuthRequired("No Kaya session - run `sync auth kaya-login`.") from exc


def login_with_credentials(
    email: str, password: str, tokenstore_dir: Path, base_url: str = KAYA_API_BASE
) -> KayaTokens:
    """The ONLY place Kaya credentials are ever used. Persists tokens, never the password."""
    response = _post("/api/user/login", {"email": email, "password": password}, base_url)
    if response.status_code in (400, 401, 403):
        raise KayaInvalidCredentials("Kaya rejected that email/password.")
    response.raise_for_status()
    body = response.json()
    try:
        tokens = KayaTokens(
            token=str(body["token"]),
            refresh_token=str(body["refresh_token"]),
            user_id=str(body["user"]["id"]),
            saved_at=datetime.now(UTC).isoformat(),
        )
    except (KeyError, TypeError) as exc:
        raise RuntimeError("Unexpected Kaya login response shape.") from exc
    save_tokens(tokenstore_dir, tokens)
    return tokens


def refresh_access_token(tokenstore_dir: Path, base_url: str = KAYA_API_BASE) -> KayaTokens:
    """Exchange the stored refresh token for a new access token. Never uses credentials."""
    tokens = load_tokens(tokenstore_dir)
    response = _post("/api/user/refresh-token", {"refresh_token": tokens.refresh_token}, base_url)
    if response.status_code in (400, 401, 403):
        raise KayaAuthRequired("Kaya session expired - run `sync auth kaya-login`.")
    response.raise_for_status()
    try:
        new_token = str(response.json()["token"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Unexpected Kaya refresh response shape.") from exc
    refreshed = KayaTokens(
        token=new_token,
        refresh_token=tokens.refresh_token,
        user_id=tokens.user_id,
        saved_at=datetime.now(UTC).isoformat(),
    )
    save_tokens(tokenstore_dir, refreshed)
    return refreshed


def token_status(tokenstore_dir: Path) -> tuple[bool, int | None]:
    """(present, age_in_days) -- a pure filesystem check, no network call."""
    try:
        tokens = load_tokens(tokenstore_dir)
    except KayaAuthRequired:
        return False, None
    saved = datetime.fromisoformat(tokens.saved_at)
    return True, (datetime.now(UTC) - saved).days
