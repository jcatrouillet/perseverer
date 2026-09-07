"""The Eufy Life adapter: syncs body-composition readings (weight, body fat, muscle mass, and
~20 other fields -- see health/eufy_parser.py) from a Eufy smart scale, via the same Eufy Life
mobile-app API the sibling `eufy-health-sync` project already uses in production to upload this
data *into* Garmin/intervals.icu. That project is this adapter's own source of truth for the
API's real shape (endpoints, auth flow, field names) -- ported directly, not reimplemented from
scratch, though this adapter reads *every* field the API returns rather than the 9 that project
extracts (see the parser's own docstring).

Unlike `garmin_connect.py`, this is deliberately NOT built around a persisted token store with
"never auto-login" as a hard rule -- there's no evidence Eufy's API shares Garmin's SSO 429-lockout
fragility, and the sibling project has run a fresh email/password login on every scheduled run for
months without incident. `sync_eufy()` simply skips (logs, doesn't raise) when credentials aren't
configured, so a deployment with no Eufy account attached is unaffected.

The API's `last_device_data` endpoint always returns the athlete's *entire* reading history in one
call (confirmed live: the `limit` query param is silently ignored) -- so there's no separate
backfill-vs-incremental distinction the way Garmin has garmin_export vs garmin_connect. Every run
reprocesses the full history; `archive_raw_bytes`' own content-addressing and
`ingest_health_batch`'s own idempotent upsert make repeat runs cheap no-ops for unchanged readings.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import Connection, select

from perseverer.adapters.fit_folder import IngestRunSummary
from perseverer.archive import archive_raw_bytes
from perseverer.db.schema import athlete_eufy_config
from perseverer.health.eufy_parser import parse_eufy_scale_reading
from perseverer.health.ingest import ingest_health_batch
from perseverer.rollups import refresh_daily_and_period_rollups

logger = logging.getLogger(__name__)

SOURCE_NAME = "eufy"
_API_BASE = "https://api.eufylife.com"
_CLIENT_ID = "eufy-app"
_CLIENT_SECRET = "8FHf22gaTKu7MZXqz5zytw"
_HEADERS = {
    "User-Agent": "EufyLife-iOS-3.3.7",
    "Category": "Health",
    "Content-Type": "application/json",
    "Accept": "*/*",
}


class EufyAuthError(Exception):
    """Login failed -- bad credentials or the API rejected them."""


class EufyClient:
    def __init__(self, email: str, password: str, device_id: str, customer_id: str) -> None:
        self.email = email
        self.password = password
        self.device_id = device_id
        self.customer_id = customer_id
        self._token: str | None = None

    def login(self) -> None:
        resp = requests.post(
            f"{_API_BASE}/v1/user/v2/email/login",
            json={
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
                "email": self.email,
                "password": self.password,
            },
            headers=_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("res_code") != 1:
            raise EufyAuthError(f"Eufy login failed: {data.get('message')}")
        self._token = data["access_token"]

    def fetch_all_readings(self) -> list[dict[str, Any]]:
        """Returns every reading the account has, newest first (the API's own order) -- the
        `limit` param is real in shape but not honored by the live endpoint, confirmed directly."""
        assert self._token is not None, "call login() first"
        resp = requests.get(
            f"{_API_BASE}/v1/device/last_device_data",
            params={"deviceId": self.device_id, "customerId": self.customer_id},
            headers={**_HEADERS, "token": self._token},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return list(data.get("data") or [])


def resolve_eufy_credentials(
    conn: Connection,
    athlete_id: str,
    *,
    legacy_athlete_id: str,
    legacy_email: str | None,
    legacy_password: str | None,
    legacy_device_id: str | None,
    legacy_customer_id: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Resolve one athlete's Eufy credentials for a sync call: a per-athlete `athlete_eufy_config`
    row (see db/schema.py) takes priority. When no row exists and `athlete_id ==
    legacy_athlete_id`, falls back to the original global env-var credentials
    (`Settings.eufy_*`) -- this is what lets the pre-existing single-athlete deployment keep
    working unchanged with zero migration required, now that a second athlete's credentials live
    in the DB instead. Any other athlete with no DB row gets all-`None`, which `sync_eufy` above
    already treats as "not configured, skip" rather than an error.
    """
    row = conn.execute(
        select(athlete_eufy_config).where(athlete_eufy_config.c.athlete_id == athlete_id)
    ).fetchone()
    if row is not None:
        return row.email, row.password, row.device_id, row.customer_id
    if athlete_id == legacy_athlete_id:
        return legacy_email, legacy_password, legacy_device_id, legacy_customer_id
    return None, None, None, None


def sync_eufy(
    conn: Connection,
    archive_root: Path,
    parquet_dir: Path,
    *,
    athlete_id: str,
    email: str | None,
    password: str | None,
    device_id: str | None,
    customer_id: str | None,
    client_factory: Callable[[str, str, str, str], EufyClient] = EufyClient,
) -> IngestRunSummary:
    summary = IngestRunSummary(run_id=0)

    if not (email and password and device_id and customer_id):
        logger.info("Eufy sync skipped: no credentials configured")
        return summary

    client = client_factory(email, password, device_id, customer_id)
    try:
        client.login()
    except (EufyAuthError, requests.RequestException) as e:
        summary.errors.append({"error": str(e)})
        return summary

    try:
        readings = client.fetch_all_readings()
    except requests.RequestException as e:
        summary.errors.append({"error": str(e)})
        return summary

    touched_dates: set[str] = set()
    for record in readings:
        summary.items_seen += 1
        record_id = str(record.get("id", ""))
        try:
            content = json.dumps(record).encode("utf-8")
            archive_raw_bytes(
                conn,
                archive_root,
                athlete_id=athlete_id,
                source=SOURCE_NAME,
                kind="eufy_scale_reading_json",
                content=content,
                locator=f"reading/{record_id}",
                external_id=record_id or None,
            )
            batch = parse_eufy_scale_reading(content)
            result = ingest_health_batch(
                conn, parquet_dir, athlete_id=athlete_id, source=SOURCE_NAME, batch=batch
            )
            conn.commit()
            touched_dates |= result.affected_local_dates
            if result.observations_new > 0:
                summary.items_new += 1
        except Exception as e:  # one bad reading must not abort the whole sync
            conn.rollback()
            summary.errors.append({"reading": record_id, "error": str(e)})

    # Only the daily/period rollups -- refresh_fitness_rollup/refresh_insights/refresh_vdot all
    # consume activity data (training load, efforts, running pace), none of which body
    # composition touches, so calling them here would be pure wasted work.
    refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates=touched_dates)
    conn.commit()

    return summary
