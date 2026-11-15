"""Garmin Connect's Personal API: reading the athlete's own data with an API key instead of a login.

Garmin lets an account holder create a *personal API key* on Garmin Connect Web (valid 30 days to a
year, shown once, revocable at any time). The key is exchanged at the token endpoint for a bearer
token valid 24 hours; there is no refresh token, a new token is simply requested. The key is read
only and carries no password, so it is the safer way to connect, and the only one that works for an
account whose login needs an MFA code the Settings form cannot answer.

This module is deliberately a stand-in for the part of `garminconnect.Garmin` that the sync uses
(`get_activities_by_date`, `download_activity`, `get_stats`, `get_sleep_data`, ...): the adapter,
the parsers and the archive keep their one code path, and the fix for a vendor change stays here.
It raises the same exception types `garminconnect` does (`GarminConnectTooManyRequestsError`,
`GarminConnectNotFoundError`, `GarminConnectAuthenticationError`), so a 429 still aborts the whole
run with no retry.

Verified against Garmin's published OpenAPI document for the Personal API and its reference client
(`github.com/garmin/connect-personal-api-python`, Apache-2.0): the token endpoint answers with the
bare JWT as its body, and every other call sends `Authorization: Bearer <jwt>`.

What the Personal API does not offer, and therefore what a key-connected account lacks: training
readiness, training status (VO2max trend, acclimation), race predictions, lactate threshold,
per-set strength data, the per-night sleep stage timeline, and any write (workouts, courses). The
sync skips those instead of failing (`PersonalApiClient.supports`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

DEFAULT_BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
KEY_FILENAME = "personal_api_key.json"
PAGE_SIZE = 99  # the search endpoint's own default and ceiling for `limit`
MAX_PAGES = 200  # ~20,000 activities: a runaway-loop guard, never reached in practice
TIMEOUT_S = 60.0

# Sync steps whose data the Personal API does not expose; the sync skips them for a key.
UNSUPPORTED = frozenset(
    {"training_readiness", "training_status", "race_predictions", "lactate_threshold", "sets"}
)


class PersonalApiReadOnly(Exception):
    """Something that writes to Garmin (a workout, a course) was asked of a key-connected
    account. The Personal API only reads; pushing needs the account login."""


def key_path(tokenstore_dir: Path) -> Path:
    return tokenstore_dir / KEY_FILENAME


def has_api_key(tokenstore_dir: Path) -> bool:
    return key_path(tokenstore_dir).is_file()


def save_api_key(tokenstore_dir: Path, api_key: str, *, base_url: str = DEFAULT_BASE_URL) -> None:
    """Stores the key in the athlete's token directory (under /data, mode 0600 where the platform
    has modes). Never logged, never returned by the API."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("the API key is empty")
    tokenstore_dir.mkdir(parents=True, exist_ok=True)
    path = key_path(tokenstore_dir)
    payload = {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "saved_at": datetime.now(UTC).isoformat(),
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def load_api_key(tokenstore_dir: Path) -> tuple[str, str]:
    """`(api_key, base_url)`. Raises `GarminConnectAuthenticationError` when the file is absent or
    unreadable, which the adapter reports as "log in again"."""
    try:
        data = json.loads(key_path(tokenstore_dir).read_text(encoding="utf-8"))
        key = data["api_key"]
        base_url = data.get("base_url") or DEFAULT_BASE_URL
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise GarminConnectAuthenticationError("No readable Garmin personal API key") from e
    if not isinstance(key, str) or not key.strip() or not isinstance(base_url, str):
        raise GarminConnectAuthenticationError("No readable Garmin personal API key")
    return key, base_url


def delete_api_key(tokenstore_dir: Path) -> bool:
    path = key_path(tokenstore_dir)
    if not path.is_file():
        return False
    path.unlink()
    return True


def api_key_age_days(tokenstore_dir: Path) -> int | None:
    path = key_path(tokenstore_dir)
    if not path.is_file():
        return None
    return (datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)).days


def _iso(value: str | date) -> str:
    return value.isoformat() if isinstance(value, date) else value


class PersonalApiClient:
    """The sync-facing subset of `garminconnect.Garmin`, served by the Personal API."""

    personal_api = True

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        http: httpx.Client | None = None,
        throttle: Callable[[], None] | None = None,
    ) -> None:
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=TIMEOUT_S)
        # The adapter waits once before each call it makes; a call that takes several requests
        # (a paged list) goes through this for every request after the first.
        self._throttle = throttle or (lambda: None)
        self._bearer: str | None = None

    @staticmethod
    def supports(feature: str) -> bool:
        return feature not in UNSUPPORTED

    # -- authentication ---------------------------------------------------------------------

    def authenticate(self) -> None:
        """Exchanges the key for a 24 h bearer token. A rejected key (revoked, expired, mistyped)
        is an authentication error; there is no credential to fall back to."""
        try:
            response = self._http.post(
                self.base_url + TOKEN_PATH,
                headers={"Accept": "*/*", "Authorization": f"ApiKey {self._api_key}"},
            )
        except httpx.HTTPError as e:
            raise GarminConnectConnectionError(f"Garmin token request failed: {e}") from e
        if response.status_code == 429:
            raise GarminConnectTooManyRequestsError("429 from the Garmin token endpoint")
        if response.status_code in (400, 401, 403):
            raise GarminConnectAuthenticationError(
                "Garmin rejected the personal API key (revoked, expired or mistyped)."
            )
        if response.status_code >= 400:
            raise GarminConnectConnectionError(
                f"Garmin token endpoint answered HTTP {response.status_code}"
            )
        token = response.text.strip().strip('"')
        if token.count(".") != 2:  # a JWT; anything else is not a token we can use
            raise GarminConnectAuthenticationError("Garmin did not return an access token.")
        self._bearer = token

    # -- transport --------------------------------------------------------------------------

    def _get(
        self, path: str, params: dict[str, Any] | None = None, *, accept: str = "application/json"
    ) -> httpx.Response:
        if self._bearer is None:
            self.authenticate()
        try:
            response = self._http.get(
                self.base_url + path,
                params=params,
                headers={"Accept": accept, "Authorization": f"Bearer {self._bearer}"},
            )
        except httpx.HTTPError as e:
            raise GarminConnectConnectionError(f"Garmin request failed: {e}") from e
        if response.status_code == 429:
            raise GarminConnectTooManyRequestsError(f"429 from Garmin on GET {path}")
        if response.status_code == 404:
            raise GarminConnectNotFoundError(f"404 from Garmin on GET {path}")
        if response.status_code in (401, 403):
            raise GarminConnectAuthenticationError(
                "Garmin refused the access token; the personal API key may have been revoked."
            )
        if response.status_code >= 400:
            raise GarminConnectConnectionError(f"Garmin answered HTTP {response.status_code}")
        return response

    def _json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._get(path, params)
        if not response.content:  # 204: Garmin has nothing for that day
            return None
        return response.json()

    # -- the Garmin methods the sync calls ---------------------------------------------------

    def get_activities_by_date(self, start: str | date, end: str | date) -> list[dict[str, Any]]:
        """Every activity summary between two local dates, newest first, paged."""
        activities: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            if page:
                self._throttle()
            batch = self._json(
                "/activitylist-service/activities/search/activities",
                {
                    "startDate": _iso(start),
                    "endDate": _iso(end),
                    "start": page * PAGE_SIZE,
                    "limit": PAGE_SIZE,
                },
            )
            if not isinstance(batch, list) or not batch:
                break
            activities.extend(a for a in batch if isinstance(a, dict))
            if len(batch) < PAGE_SIZE:
                break
        return activities

    def download_activity(self, activity_id: str | int, dl_fmt: Any = None) -> bytes:
        """The original activity file (a zip holding the FIT file, which the adapter unwraps)."""
        response = self._get(
            f"/download-service/files/activity/{activity_id}",
            accept="application/zip, application/x-zip-compressed, application/octet-stream",
        )
        if not response.content:
            raise GarminConnectConnectionError("Garmin returned an empty activity file")
        return response.content

    def get_stats(self, local_date: str) -> Any:
        return self._json("/usersummary-service/usersummary/daily", {"calendarDate": local_date})

    def get_sleep_data(self, local_date: str) -> dict[str, Any]:
        """Wrapped as `{"dailySleepDTO": ...}`, the shape `parse_daily_sleep_json` reads. The
        Personal API returns the totals and the score but not the stage timeline, so a night read
        this way has no stage segments."""
        daily = self._json("/sleep-service/sleep/dailySleep", {"date": local_date})
        return {"dailySleepDTO": daily if isinstance(daily, dict) else None}

    def get_hrv_data(self, local_date: str) -> Any:
        return self._json(f"/hrv-service/hrv/{local_date}")

    def get_hydration_data(self, local_date: str) -> dict[str, Any]:
        days = self._json(f"/usersummary-service/stats/hydration/daily/{local_date}/{local_date}")
        day = days[0] if isinstance(days, list) and days and isinstance(days[0], dict) else {}
        return {"calendarDate": local_date, **day}

    def get_body_battery(self, start: str, end: str) -> Any:
        """Daily Body Battery reports (at most 31 days per request, by Garmin's own limit)."""
        return self._json(
            "/wellness-service/wellness/bodyBattery/reports/daily",
            {"startDate": start, "endDate": end},
        )


def connect_with_api_key(
    tokenstore_dir: Path,
    api_key: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    http: httpx.Client | None = None,
) -> None:
    """Checks the key (one token exchange) and only then stores it, so a mistyped, revoked or
    expired key never replaces a working connection. Raises the `garminconnect` exceptions
    `PersonalApiClient.authenticate` does."""
    api_key = api_key.strip()
    if not api_key:
        raise GarminConnectAuthenticationError("The API key is empty.")
    PersonalApiClient(api_key, base_url=base_url, http=http).authenticate()
    save_api_key(tokenstore_dir, api_key, base_url=base_url)
