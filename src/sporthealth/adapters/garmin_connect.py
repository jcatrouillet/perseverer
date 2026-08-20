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
from sporthealth.fitness import refresh_fitness_rollup
from sporthealth.health.ingest import HealthIngestResult, ingest_health_batch
from sporthealth.health.json_parser import (
    parse_daily_hrv_json,
    parse_daily_race_predictions_json,
    parse_daily_sleep_json,
    parse_daily_summary_json,
    parse_daily_training_readiness_json,
    parse_daily_training_status_json,
    parse_hydration_json,
)
from sporthealth.insights.engine import refresh_insights
from sporthealth.pace_bands import refresh_pace_bands
from sporthealth.performance import refresh_vdot
from sporthealth.rollups import refresh_daily_and_period_rollups

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

    def fetch_and_ingest_daily_wellness(
        self,
        conn: Connection,
        archive_root: Path,
        parquet_dir: Path,
        *,
        athlete_id: str,
        local_date: str,
    ) -> HealthIngestResult:
        """One calendar day's daily-summary JSON (`get_stats` -- steps, resting/max HR, calories,
        floors, SpO2, stress) -- the exact same JSON shape `fit_folder.py` already parses via
        `parse_daily_summary_json` for a `daily_summary_*.json` file dropped in a watched folder,
        just fetched live instead of from a file. No new parser needed."""
        assert self._client is not None, "call authenticate() first"
        self.rate_limiter.wait()
        try:
            stats = self._client.get_stats(local_date)
        except GarminConnectTooManyRequestsError as e:
            raise GarminRateLimitAborted(
                f"429 from Garmin while fetching wellness for {local_date}"
            ) from e

        content = json.dumps(stats).encode("utf-8")
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="garmin_connect_daily_summary_json",
            content=content,
            locator=f"daily-summary/{local_date}",
        )
        return ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            batch=parse_daily_summary_json(content),
        )

    def fetch_and_ingest_daily_sleep(
        self,
        conn: Connection,
        archive_root: Path,
        parquet_dir: Path,
        *,
        athlete_id: str,
        local_date: str,
    ) -> HealthIngestResult:
        """One calendar day's sleep (`get_sleep_data` -- total/stage durations, sleep score,
        overnight respiration/SpO2/HR/stress) -- previously never fetched by this adapter at
        all, which is why sleep silently stopped the moment the last garmin_export backfill's
        own data ran out even though the daily sync itself kept succeeding (only fit_folder and
        garmin_export ever produced sleep_session rows, both from monitoring FIT files, not this
        adapter). See health/json_parser.py::parse_daily_sleep_json for the response shape."""
        assert self._client is not None, "call authenticate() first"
        self.rate_limiter.wait()
        try:
            sleep_data = self._client.get_sleep_data(local_date)
        except GarminConnectTooManyRequestsError as e:
            raise GarminRateLimitAborted(
                f"429 from Garmin while fetching sleep for {local_date}"
            ) from e

        content = json.dumps(sleep_data).encode("utf-8")
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="garmin_connect_daily_sleep_json",
            content=content,
            locator=f"daily-sleep/{local_date}",
        )
        return ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            batch=parse_daily_sleep_json(content),
        )

    def fetch_and_ingest_daily_hrv(
        self,
        conn: Connection,
        archive_root: Path,
        parquet_dir: Path,
        *,
        athlete_id: str,
        local_date: str,
    ) -> HealthIngestResult:
        """One calendar day's HRV summary (`get_hrv_data` -- weekly/last-night average, status)
        -- same story as fetch_and_ingest_daily_sleep above: this adapter never fetched HRV at
        all, so it silently stopped the moment the last garmin_export backfill's own data ran
        out even though the daily sync itself kept succeeding. See
        health/json_parser.py::parse_daily_hrv_json for the response shape."""
        assert self._client is not None, "call authenticate() first"
        self.rate_limiter.wait()
        try:
            hrv_data = self._client.get_hrv_data(local_date)
        except GarminConnectTooManyRequestsError as e:
            raise GarminRateLimitAborted(
                f"429 from Garmin while fetching HRV for {local_date}"
            ) from e

        content = json.dumps(hrv_data).encode("utf-8")
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="garmin_connect_daily_hrv_json",
            content=content,
            locator=f"daily-hrv/{local_date}",
        )
        return ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            batch=parse_daily_hrv_json(content),
        )

    def fetch_and_ingest_daily_training_readiness(
        self,
        conn: Connection,
        archive_root: Path,
        parquet_dir: Path,
        *,
        athlete_id: str,
        local_date: str,
    ) -> HealthIngestResult:
        """One calendar day's training readiness (`get_training_readiness` -- score, level,
        the HRV/sleep/stress/ACWR factors behind it) -- another metric this adapter never
        fetched at all, same story as sleep/HRV above (only garmin_export's own
        TrainingReadinessDTO report ever produced this, and that stopped the moment the last
        backfill ran out). See health/json_parser.py::parse_daily_training_readiness_json."""
        assert self._client is not None, "call authenticate() first"
        self.rate_limiter.wait()
        try:
            readiness = self._client.get_training_readiness(local_date)
        except GarminConnectTooManyRequestsError as e:
            raise GarminRateLimitAborted(
                f"429 from Garmin while fetching training readiness for {local_date}"
            ) from e

        content = json.dumps(readiness).encode("utf-8")
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="garmin_connect_daily_training_readiness_json",
            content=content,
            locator=f"daily-training-readiness/{local_date}",
        )
        return ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            batch=parse_daily_training_readiness_json(content),
        )

    def fetch_and_ingest_daily_training_status(
        self,
        conn: Connection,
        archive_root: Path,
        parquet_dir: Path,
        *,
        athlete_id: str,
        local_date: str,
    ) -> HealthIngestResult:
        """One calendar day's training status (`get_training_status` -- VO2max, heat/altitude
        acclimation, and training status/fitness trend, all in one response). Covers *three*
        GDPR-export report kinds this adapter never fetched live (MetricsMaxMetData,
        MetricsHeatAltitudeAcclimation, TrainingHistory) -- see
        health/json_parser.py::parse_daily_training_status_json for exactly how they map."""
        assert self._client is not None, "call authenticate() first"
        self.rate_limiter.wait()
        try:
            status = self._client.get_training_status(local_date)
        except GarminConnectTooManyRequestsError as e:
            raise GarminRateLimitAborted(
                f"429 from Garmin while fetching training status for {local_date}"
            ) from e

        content = json.dumps(status).encode("utf-8")
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="garmin_connect_daily_training_status_json",
            content=content,
            locator=f"daily-training-status/{local_date}",
        )
        return ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            batch=parse_daily_training_status_json(content),
        )

    def fetch_and_ingest_daily_hydration(
        self,
        conn: Connection,
        archive_root: Path,
        parquet_dir: Path,
        *,
        athlete_id: str,
        local_date: str,
    ) -> HealthIngestResult:
        """One calendar day's hydration (`get_hydration_data` -- same daily-aggregate JSON
        shape fit_folder.py's own `parse_hydration_json` already parses for a dropped
        `hydration_*.json` file, just fetched live instead of from a file -- no new parser
        needed, same reuse as fetch_and_ingest_daily_wellness's daily-summary shape). Never
        fetched by this adapter before now."""
        assert self._client is not None, "call authenticate() first"
        self.rate_limiter.wait()
        try:
            hydration = self._client.get_hydration_data(local_date)
        except GarminConnectTooManyRequestsError as e:
            raise GarminRateLimitAborted(
                f"429 from Garmin while fetching hydration for {local_date}"
            ) from e

        content = json.dumps(hydration).encode("utf-8")
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="garmin_connect_daily_hydration_json",
            content=content,
            locator=f"daily-hydration/{local_date}",
        )
        return ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            batch=parse_hydration_json(content),
        )

    def fetch_and_ingest_race_predictions(
        self,
        conn: Connection,
        archive_root: Path,
        parquet_dir: Path,
        *,
        athlete_id: str,
        since: datetime,
        until: datetime,
    ) -> HealthIngestResult:
        """Race-time predictions (`get_race_predictions`, `_type="daily"`) for the whole
        rolling window in *one* request -- unlike every other daily fetch in this adapter, this
        endpoint itself accepts a date range, so there's no reason to call it once per day. See
        health/json_parser.py::parse_daily_race_predictions_json."""
        assert self._client is not None, "call authenticate() first"
        self.rate_limiter.wait()
        start_date = since.date().isoformat()
        end_date = until.date().isoformat()
        try:
            predictions = self._client.get_race_predictions(
                startdate=start_date, enddate=end_date, _type="daily"
            )
        except GarminConnectTooManyRequestsError as e:
            raise GarminRateLimitAborted("429 from Garmin while fetching race predictions") from e

        content = json.dumps(predictions).encode("utf-8")
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="garmin_connect_race_predictions_json",
            content=content,
            locator=f"race-predictions/{start_date}_{end_date}",
        )
        return ingest_health_batch(
            conn,
            parquet_dir,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            batch=parse_daily_race_predictions_json(content),
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
    touched_dates: set[str] = set()
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
                if ingest_result.local_date is not None:
                    touched_dates.add(ingest_result.local_date)
                if ingest_result.created:
                    summary.items_new += 1
            except GarminRateLimitAborted as e:
                conn.rollback()
                summary.errors.append({"activity": activity_id, "error": str(e)})
                break  # no retry loop — stop this run entirely, let the next scheduled run continue

        # Daily wellness (steps, resting/max HR, calories, floors, SpO2, stress) -- the whole
        # rolling window every run, not just "new" days: ingest_health_batch already upserts
        # idempotently (same call fit_folder.py makes for this identical JSON shape), so a day
        # that already has current data is a cheap no-op, and a day that failed to ingest on a
        # previous run self-heals here without needing separate staleness tracking.
        wellness_date = since.date()
        today = datetime.now(UTC).date()
        while wellness_date <= today:
            try:
                health_result = adapter.fetch_and_ingest_daily_wellness(
                    conn,
                    archive_root,
                    parquet_dir,
                    athlete_id=athlete_id,
                    local_date=wellness_date.isoformat(),
                )
                conn.commit()
                touched_dates |= health_result.affected_local_dates
            except GarminRateLimitAborted as e:
                conn.rollback()
                summary.errors.append(
                    {"wellness_date": wellness_date.isoformat(), "error": str(e)}
                )
                break  # no retry loop — same contract as the activity loop above
            wellness_date += timedelta(days=1)

        # Sleep -- same whole-rolling-window, idempotent-upsert, self-healing contract as daily
        # wellness above (a night already ingested is a cheap no-op; a night whose fetch failed
        # on a previous run just gets retried here on the next one).
        sleep_date = since.date()
        while sleep_date <= today:
            try:
                sleep_result = adapter.fetch_and_ingest_daily_sleep(
                    conn,
                    archive_root,
                    parquet_dir,
                    athlete_id=athlete_id,
                    local_date=sleep_date.isoformat(),
                )
                conn.commit()
                touched_dates |= sleep_result.affected_local_dates
            except GarminRateLimitAborted as e:
                conn.rollback()
                summary.errors.append({"sleep_date": sleep_date.isoformat(), "error": str(e)})
                break  # no retry loop — same contract as the loops above
            sleep_date += timedelta(days=1)

        # HRV -- same whole-rolling-window, idempotent-upsert, self-healing contract as sleep
        # and daily wellness above.
        hrv_date = since.date()
        while hrv_date <= today:
            try:
                hrv_result = adapter.fetch_and_ingest_daily_hrv(
                    conn,
                    archive_root,
                    parquet_dir,
                    athlete_id=athlete_id,
                    local_date=hrv_date.isoformat(),
                )
                conn.commit()
                touched_dates |= hrv_result.affected_local_dates
            except GarminRateLimitAborted as e:
                conn.rollback()
                summary.errors.append({"hrv_date": hrv_date.isoformat(), "error": str(e)})
                break  # no retry loop — same contract as the loops above
            hrv_date += timedelta(days=1)

        # Training readiness -- same whole-rolling-window, idempotent-upsert, self-healing
        # contract as sleep/HRV/wellness above.
        readiness_date = since.date()
        while readiness_date <= today:
            try:
                readiness_result = adapter.fetch_and_ingest_daily_training_readiness(
                    conn,
                    archive_root,
                    parquet_dir,
                    athlete_id=athlete_id,
                    local_date=readiness_date.isoformat(),
                )
                conn.commit()
                touched_dates |= readiness_result.affected_local_dates
            except GarminRateLimitAborted as e:
                conn.rollback()
                summary.errors.append(
                    {"readiness_date": readiness_date.isoformat(), "error": str(e)}
                )
                break  # no retry loop — same contract as the loops above
            readiness_date += timedelta(days=1)

        # Training status (VO2max, heat/altitude acclimation, fitness trend) -- same contract.
        training_status_date = since.date()
        while training_status_date <= today:
            try:
                status_result = adapter.fetch_and_ingest_daily_training_status(
                    conn,
                    archive_root,
                    parquet_dir,
                    athlete_id=athlete_id,
                    local_date=training_status_date.isoformat(),
                )
                conn.commit()
                touched_dates |= status_result.affected_local_dates
            except GarminRateLimitAborted as e:
                conn.rollback()
                summary.errors.append(
                    {"training_status_date": training_status_date.isoformat(), "error": str(e)}
                )
                break  # no retry loop — same contract as the loops above
            training_status_date += timedelta(days=1)

        # Hydration -- same contract.
        hydration_date = since.date()
        while hydration_date <= today:
            try:
                hydration_result = adapter.fetch_and_ingest_daily_hydration(
                    conn,
                    archive_root,
                    parquet_dir,
                    athlete_id=athlete_id,
                    local_date=hydration_date.isoformat(),
                )
                conn.commit()
                touched_dates |= hydration_result.affected_local_dates
            except GarminRateLimitAborted as e:
                conn.rollback()
                summary.errors.append(
                    {"hydration_date": hydration_date.isoformat(), "error": str(e)}
                )
                break  # no retry loop — same contract as the loops above
            hydration_date += timedelta(days=1)

        # Race predictions -- one range request for the whole window, not a per-day loop (see
        # fetch_and_ingest_race_predictions's own docstring for why this endpoint is different).
        try:
            race_result = adapter.fetch_and_ingest_race_predictions(
                conn,
                archive_root,
                parquet_dir,
                athlete_id=athlete_id,
                since=since,
                until=started_at,
            )
            conn.commit()
            touched_dates |= race_result.affected_local_dates
        except GarminRateLimitAborted as e:
            conn.rollback()
            summary.errors.append({"error": f"race predictions: {e}"})
    except (GarminAuthRequired, GarminRateLimitAborted) as e:
        summary.errors.append({"error": str(e)})

    refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates=touched_dates)
    # Unconditional, unlike the other four ingest entry points: this is the daily scheduled
    # sync path (worker/main.py runs it once/day regardless of whether new activities were
    # found), so the Fitness & Form series' end date must keep advancing through rest days --
    # see fitness.py and docs/adr/0009-phase-6-calendar-rollups-fitness-health.md. The same
    # reasoning is why refresh_insights is unconditional here too (ADR 0012): a window like
    # "last 30 days" shifts every day even with zero new ingests, and this daily cron run is
    # this codebase's only naturally-daily trigger point -- no separate scheduled job needed.
    refresh_fitness_rollup(conn, athlete_id=athlete_id)
    refresh_insights(conn, athlete_id=athlete_id)
    # Unlike fitness/insights above, VDOT has no rolling-window dependency on "today" -- it's a
    # pure per-activity value, so a rest day with zero new activities has nothing to recompute.
    # Gated on touched_dates to keep the daily cron cheap (no Parquet reads) on those days.
    if touched_dates:
        refresh_vdot(conn, parquet_dir, athlete_id=athlete_id)
        refresh_pace_bands(conn, parquet_dir, athlete_id=athlete_id)
    conn.commit()

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
