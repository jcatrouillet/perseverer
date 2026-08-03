"""The garmin_connect adapter: the primary, incremental, unattended sync path.

Hard requirements from the project spec, all enforced here:
- **One login per token lifetime, not one per run.** `authenticate()` only ever loads the
  existing token store — it constructs `Garmin()` with no credentials, so if the token store
  is missing/expired/corrupt, the library's own `login()` has nothing to fall back to and
  raises `GarminConnectAuthenticationError`, which we re-raise as `GarminAuthRequired`. There
  is no code path from this adapter back to a credentialed login. Credentials are only ever
  used interactively, in `sync auth login` (see cli.py) — never here.
- **Abort on first 429, never retry-loop.** `GarminConnectTooManyRequestsError` stops the run
  immediately.
- **Always archive the original FIT, not just the JSON summary**, for every activity — the
  FIT is what actually gets parsed (via the same `parse_fit` as `fit_folder`/`garmin_export`);
  the JSON summary is archived too but not cross-referenced from `activity_source_link` (see
  docs/adr/0003-phase-2-garmin-adapters.md).

API verified against the installed `garminconnect` package (introspected directly, not
recalled from training data — this library changes fast and the project spec says so
explicitly): `Garmin(email, password, prompt_mfa=...)`, `login(tokenstore=path)`,
`get_activities_by_date(start, end)`, `download_activity(activity_id,
dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)`.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)
from sqlalchemy import Connection

from sporthealth.adapters.base import AdapterHealth
from sporthealth.adapters.fit_folder import IngestResult, IngestRunSummary, ingest_canonical_batch
from sporthealth.archive import archive_raw_bytes
from sporthealth.db.schema import ingest_run
from sporthealth.fit.parser import parse_fit

SOURCE_NAME = "garmin_connect"


class GarminAuthRequired(Exception):
    """No valid token store. Only `sync auth login`, run interactively by a human, can fix
    this — never automatically retried from here."""


class GarminRateLimitAborted(Exception):
    """Garmin returned 429. The run stops immediately; no retry, no backoff-and-continue."""


class RateLimiter:
    """Enforces a minimum interval between requests and a hard cap per rolling hour. Hitting
    the hourly cap stops the current run cleanly (the next scheduled run continues) rather
    than sleeping until capacity frees up — a sync run should finish in minutes, not hours.
    """

    def __init__(self, interval_s: float, max_per_hour: int) -> None:
        self.interval_s = interval_s
        self.max_per_hour = max_per_hour
        self._request_times: list[float] = []

    def wait(self) -> None:
        now = time.monotonic()
        self._request_times = [t for t in self._request_times if now - t < 3600]
        if len(self._request_times) >= self.max_per_hour:
            raise GarminRateLimitAborted(
                f"Hit the {self.max_per_hour}/hour cap for this run; stopping here."
            )
        if self._request_times:
            elapsed = now - self._request_times[-1]
            if elapsed < self.interval_s:
                time.sleep(self.interval_s - elapsed)
        self._request_times.append(time.monotonic())


def _unwrap_fit_bytes(content: bytes) -> bytes:
    """Garmin's "original" download endpoint sometimes wraps the FIT file in a zip and
    sometimes doesn't — checked by content, not assumed either way."""
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            fit_names = [n for n in zf.namelist() if n.lower().endswith(".fit")]
            if not fit_names:
                raise ValueError("ORIGINAL download was a zip with no .fit member inside")
            return zf.read(fit_names[0])
    return content


class GarminConnectAdapter:
    name = SOURCE_NAME

    def __init__(
        self,
        tokenstore_dir: Path,
        rate_limiter: RateLimiter,
        client_factory: Callable[[], Garmin] = Garmin,
    ) -> None:
        self.tokenstore_dir = tokenstore_dir
        self.rate_limiter = rate_limiter
        self._client_factory = client_factory
        self._client: Garmin | None = None

    def health_check(self) -> AdapterHealth:
        if not self.tokenstore_dir.exists() or not any(self.tokenstore_dir.iterdir()):
            return AdapterHealth(ok=False, detail="no token store - run `sync auth login`")
        return AdapterHealth(ok=True)

    def authenticate(self) -> None:
        """Loads the existing token store only. Never constructs a client with credentials —
        see module docstring for why that matters."""
        client = self._client_factory()
        try:
            client.login(tokenstore=str(self.tokenstore_dir))
        except GarminConnectAuthenticationError as e:
            raise GarminAuthRequired(
                "No valid Garmin token found (or it's expired). This adapter never attempts "
                "a credentialed login automatically - run `sync auth login` interactively."
            ) from e
        self._client = client

    def list_activity_summaries(self, since: datetime) -> list[dict[str, Any]]:
        assert self._client is not None, "call authenticate() first"
        self.rate_limiter.wait()
        try:
            activities = self._client.get_activities_by_date(
                since.date().isoformat(), datetime.now(UTC).date().isoformat()
            )
        except GarminConnectTooManyRequestsError as e:
            raise GarminRateLimitAborted("429 from Garmin while listing activities") from e
        return list(activities)

    def fetch_and_ingest_activity(
        self,
        conn: Connection,
        archive_root: Path,
        parquet_dir: Path,
        *,
        athlete_id: str,
        activity_summary: dict[str, Any],
    ) -> IngestResult:
        assert self._client is not None, "call authenticate() first"
        activity_id = str(activity_summary["activityId"])

        # Archive the JSON summary regardless of what happens to the FIT download below —
        # raw-first holds even if the rest of this activity's ingest fails.
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="garmin_connect_json",
            content=json.dumps(activity_summary).encode("utf-8"),
            locator=f"activity/{activity_id}",
            external_id=activity_id,
        )

        self.rate_limiter.wait()
        try:
            raw_download = self._client.download_activity(
                activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
            )
        except GarminConnectTooManyRequestsError as e:
            raise GarminRateLimitAborted(
                f"429 from Garmin while downloading activity {activity_id}"
            ) from e
        fit_bytes = _unwrap_fit_bytes(raw_download)

        raw_fit_id = archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="fit_activity",
            content=fit_bytes,
            locator=f"activity/{activity_id}/download",
            external_id=activity_id,
        )
        sha256 = _sha256(fit_bytes)
        batch = parse_fit(fit_bytes)
        return ingest_canonical_batch(
            conn,
            parquet_dir,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            raw_object_id=raw_fit_id,
            sha256=sha256,
            batch=batch,
            external_id_hint=activity_id,
        )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class RateLimitSettings:
    request_interval_s: float
    max_requests_per_hour: int


def sync_garmin_connect(
    conn: Connection,
    archive_root: Path,
    parquet_dir: Path,
    tokenstore_dir: Path,
    *,
    athlete_id: str,
    rolling_window_days: int,
    rate_limits: RateLimitSettings,
    client_factory: Callable[[], Garmin] = Garmin,
) -> IngestRunSummary:
    """The incremental sync: rolling re-fetch window, never retries past a 429, never falls
    back to credentialed auth. Always writes an ingest_run row, even on total auth failure —
    that row is what the staleness check (sporthealth/staleness.py) looks at.

    `client_factory` defaults to the real `Garmin` class; tests inject a fake to exercise this
    whole orchestration (including ingest_run bookkeeping) with no real network access.
    """
    rate_limiter = RateLimiter(rate_limits.request_interval_s, rate_limits.max_requests_per_hour)
    adapter = GarminConnectAdapter(tokenstore_dir, rate_limiter, client_factory=client_factory)

    started_at = datetime.now(UTC)
    result = conn.execute(
        ingest_run.insert().values(
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            started_at=started_at,
            status="running",
            watermark_from=started_at - timedelta(days=rolling_window_days),
            watermark_to=started_at,
            items_seen=0,
            items_new=0,
        )
    )
    assert result.inserted_primary_key is not None
    run_id = result.inserted_primary_key[0]
    assert isinstance(run_id, int)
    conn.commit()

    summary = IngestRunSummary(run_id=run_id)
    try:
        adapter.authenticate()
        since = started_at - timedelta(days=rolling_window_days)
        activities = adapter.list_activity_summaries(since)
        for activity_summary in activities:
            summary.items_seen += 1
            activity_id = str(activity_summary.get("activityId"))
            try:
                ingest_result = adapter.fetch_and_ingest_activity(
                    conn,
                    archive_root,
                    parquet_dir,
                    athlete_id=athlete_id,
                    activity_summary=activity_summary,
                )
                conn.commit()
                if ingest_result.created:
                    summary.items_new += 1
            except GarminRateLimitAborted as e:
                conn.rollback()
                summary.errors.append({"activity": activity_id, "error": str(e)})
                break  # no retry loop — stop this run entirely, let the next scheduled run continue
    except (GarminAuthRequired, GarminRateLimitAborted) as e:
        summary.errors.append({"error": str(e)})

    conn.execute(
        ingest_run.update()
        .where(ingest_run.c.id == run_id)
        .values(
            finished_at=datetime.now(UTC),
            status="failed" if summary.errors else "success",
            items_seen=summary.items_seen,
            items_new=summary.items_new,
            errors=json.dumps(summary.errors) if summary.errors else None,
        )
    )
    conn.commit()
    return summary
