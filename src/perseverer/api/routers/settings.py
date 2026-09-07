"""GET/PUT /settings/hr-zones -- an athlete's own configured HR training zones, derived from
three reference points (max/threshold/resting HR). See api/schemas/settings.py and
db/schema.py::athlete_hr_zone_config for the shape and hr_zones.py for the derivation formula.

Also GET/PUT /settings/running-load -- an athlete's own configured running threshold pace, the
one calibration constant `running_load.py::compute_running_tss` needs to turn grade-adjusted
pace into a Coggan-style rTSS. Unlike the hr-zones PUT, this one also immediately recomputes
`running_tss`/`fitness_daily_rollup`/insights for the athlete before returning -- otherwise a
first-time save would look like a no-op until the next sync, since those are otherwise only
refreshed at ingest time (see running_load.py's own module docstring).

Also the Garmin Connect status/login/sync, rebuild, and bulk-export-upload endpoints -- the web
counterparts of `sync auth login/status`, `sync import garmin-connect`, `sync rebuild`, and
`sync import garmin-export/strava-export`. Login is the one place besides the CLI that ever
handles a Garmin password (see adapters/garmin_connect.py::login_with_credentials's own
docstring for why it's safe: human-initiated, one-shot, only the resulting token store is
persisted). Sync/rebuild/import all run via BackgroundTasks and are polled through the generic
GET /settings/jobs/latest -- there is no job-queue table in this project; `ingest_run` (already
written by every sync/import entrypoint) already is one.

Also GET/POST/DELETE /settings/calendar-feed -- publish/rotate/unpublish the athlete's own
Google-Calendar-subscribable feed of their planned_workout calendar. These are the authenticated
management routes; the public `.ics` response itself is `GET /share/calendar/{token}.ics`
(api/routers/calendar_feed.py, mounted separately with no auth at all). See calendar_feed.py's
own module docstring for why this is a single standing per-athlete secret (mirroring
athlete.api_key_hash), not a share_link-style growing history.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
from sqlalchemy import Connection, Row, desc, select
from sqlalchemy.engine import Engine

from perseverer.adapters.garmin_connect import (
    RateLimitSettings,
    login_with_credentials,
    sync_garmin_connect,
    token_store_status,
)
from perseverer.adapters.garmin_export import import_garmin_export
from perseverer.adapters.strava_export import import_strava_export
from perseverer.api.dependencies import get_conn, get_engine, require_api_key
from perseverer.api.schemas.settings import (
    CalendarFeedStatusOut,
    CalendarFeedUrlOut,
    GarminAuthStatusOut,
    GarminLoginIn,
    GarminLoginOut,
    HrZoneConfigIn,
    HrZoneConfigOut,
    JobStatusOut,
    JobTriggerOut,
    RunningLoadConfigIn,
    RunningLoadConfigOut,
)
from perseverer.calendar_feed import generate_feed_token, hash_feed_token
from perseverer.config import Settings, get_settings
from perseverer.db.schema import (
    athlete,
    athlete_hr_zone_config,
    athlete_running_load_config,
    ingest_run,
)
from perseverer.fitness import refresh_fitness_rollup
from perseverer.insights.engine import refresh_insights
from perseverer.performance_rollup import refresh_performance_rollup
from perseverer.running_load import refresh_running_tss
from perseverer.staleness import check_garmin_connect_staleness

router = APIRouter()


@router.get("/settings/hr-zones")
def get_hr_zone_config(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> HrZoneConfigOut:
    row = conn.execute(
        select(athlete_hr_zone_config).where(athlete_hr_zone_config.c.athlete_id == athlete_id)
    ).fetchone()
    if row is None:
        return HrZoneConfigOut.from_inputs(None, None, None)
    return HrZoneConfigOut.from_inputs(row.max_hr_bpm, row.threshold_hr_bpm, row.resting_hr_bpm)


@router.put("/settings/hr-zones")
def set_hr_zone_config(
    payload: HrZoneConfigIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> HrZoneConfigOut:
    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage (ADR 0002)
    existing = conn.execute(
        select(athlete_hr_zone_config.c.athlete_id).where(
            athlete_hr_zone_config.c.athlete_id == athlete_id
        )
    ).scalar_one_or_none()
    values = {
        "max_hr_bpm": payload.max_hr_bpm,
        "threshold_hr_bpm": payload.threshold_hr_bpm,
        "resting_hr_bpm": payload.resting_hr_bpm,
        "updated_at": now,
    }
    if existing is None:
        conn.execute(athlete_hr_zone_config.insert().values(athlete_id=athlete_id, **values))
    else:
        conn.execute(
            athlete_hr_zone_config.update()
            .where(athlete_hr_zone_config.c.athlete_id == athlete_id)
            .values(**values)
        )
    conn.commit()
    return HrZoneConfigOut.from_inputs(
        payload.max_hr_bpm, payload.threshold_hr_bpm, payload.resting_hr_bpm
    )


@router.get("/settings/running-load")
def get_running_load_config(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> RunningLoadConfigOut:
    row = conn.execute(
        select(athlete_running_load_config).where(
            athlete_running_load_config.c.athlete_id == athlete_id
        )
    ).fetchone()
    if row is None:
        return RunningLoadConfigOut(threshold_pace_sec_per_km=None)
    return RunningLoadConfigOut(threshold_pace_sec_per_km=row.threshold_pace_sec_per_km)


@router.put("/settings/running-load")
def set_running_load_config(
    payload: RunningLoadConfigIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> RunningLoadConfigOut:
    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage (ADR 0002)
    existing = conn.execute(
        select(athlete_running_load_config.c.athlete_id).where(
            athlete_running_load_config.c.athlete_id == athlete_id
        )
    ).scalar_one_or_none()
    values = {
        "threshold_pace_sec_per_km": payload.threshold_pace_sec_per_km,
        "updated_at": now,
    }
    if existing is None:
        conn.execute(
            athlete_running_load_config.insert().values(athlete_id=athlete_id, **values)
        )
    else:
        conn.execute(
            athlete_running_load_config.update()
            .where(athlete_running_load_config.c.athlete_id == athlete_id)
            .values(**values)
        )
    # Immediate, not deferred to the next sync -- see module docstring. Cheap: pure recomputation
    # over already-ingested data, no Parquet/network access.
    refresh_running_tss(conn, athlete_id=athlete_id)
    refresh_fitness_rollup(conn, athlete_id=athlete_id)
    refresh_performance_rollup(conn, athlete_id=athlete_id)
    refresh_insights(conn, athlete_id=athlete_id)
    conn.commit()
    return RunningLoadConfigOut(threshold_pace_sec_per_km=payload.threshold_pace_sec_per_km)


def _public_base_url(request: Request, settings: Settings) -> str:
    # Duplicated from share.py's own module-private helper of the same name -- same "duplicate
    # the small private helper" precedent this codebase already uses elsewhere (e.g. weather.py::
    # _read_cached) rather than importing a router module for a four-line function.
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get("/settings/calendar-feed")
def get_calendar_feed_status(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> CalendarFeedStatusOut:
    row = conn.execute(
        select(athlete.c.calendar_feed_token_hash, athlete.c.calendar_feed_created_at).where(
            athlete.c.id == athlete_id
        )
    ).fetchone()
    if row is None or row.calendar_feed_token_hash is None:
        return CalendarFeedStatusOut(enabled=False, created_at=None)
    created_at = row.calendar_feed_created_at.isoformat() if row.calendar_feed_created_at else None
    return CalendarFeedStatusOut(enabled=True, created_at=created_at)


@router.post("/settings/calendar-feed")
def post_calendar_feed(
    request: Request,
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> CalendarFeedUrlOut:
    # Always generates a fresh token, whether this is the first publish or a rotation -- the only
    # operation that ever makes sense here is "give me a current link" (see calendar_feed.py's
    # own module docstring for why this is one standing secret, not a growing history of shares).
    raw_token = generate_feed_token()
    now = datetime.now(UTC).replace(tzinfo=None)  # naive-implicit-UTC, matches storage (ADR 0002)
    conn.execute(
        athlete.update()
        .where(athlete.c.id == athlete_id)
        .values(calendar_feed_token_hash=hash_feed_token(raw_token), calendar_feed_created_at=now)
    )
    conn.commit()
    base = _public_base_url(request, settings)
    return CalendarFeedUrlOut(url=f"{base}/share/calendar/{raw_token}.ics")


@router.delete("/settings/calendar-feed")
def delete_calendar_feed(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> CalendarFeedStatusOut:
    conn.execute(
        athlete.update()
        .where(athlete.c.id == athlete_id)
        .values(calendar_feed_token_hash=None, calendar_feed_created_at=None)
    )
    conn.commit()
    return CalendarFeedStatusOut(enabled=False, created_at=None)


def _latest_ingest_run(conn: Connection, athlete_id: str, source: str) -> Row[Any] | None:
    return conn.execute(
        select(ingest_run)
        .where(ingest_run.c.athlete_id == athlete_id, ingest_run.c.source == source)
        .order_by(desc(ingest_run.c.started_at))
        .limit(1)
    ).fetchone()


def _first_error(errors_json: str | None) -> str | None:
    if not errors_json:
        return None
    errors = json.loads(errors_json)
    if not errors:
        return None
    first = errors[0]
    return first.get("error", str(first)) if isinstance(first, dict) else str(first)


@router.get("/settings/garmin/status")
def get_garmin_status(
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> GarminAuthStatusOut:
    present, age_days = token_store_status(settings.garmin_tokenstore_dir_for(athlete_id))
    latest = _latest_ingest_run(conn, athlete_id, "garmin_connect")
    staleness = check_garmin_connect_staleness(
        conn, athlete_id, escalate_after_days=settings.garmin_stale_escalate_days
    )
    return GarminAuthStatusOut(
        token_store_present=present,
        token_store_age_days=age_days,
        last_sync_status=latest.status if latest else None,
        last_sync_at=(latest.finished_at or latest.started_at) if latest else None,
        last_sync_error=_first_error(latest.errors) if latest else None,
        staleness_severity=staleness.severity if staleness else None,
        staleness_message=staleness.message if staleness else None,
    )


@router.post("/settings/garmin/login")
def post_garmin_login(
    payload: GarminLoginIn,
    athlete_id: Annotated[str, Depends(require_api_key)],
    settings: Settings = Depends(get_settings),
) -> GarminLoginOut:
    """A human-initiated, one-shot login -- see login_with_credentials's own docstring for why
    this is safe (only the resulting token store is persisted, never the password) and why it
    deliberately does not support Garmin's MFA challenge.

    A wrong Garmin password deliberately does NOT raise 401 -- found live: this app's own
    frontend (`api/client.ts`) treats *any* 401, from *any* endpoint, as "the athlete's own
    Perseverer session is invalid" and force-logs-them-out (clears the stored credential,
    re-shows the login screen) — exactly right for that real case, completely wrong for "you
    mistyped your Garmin password," which is a third-party credential this endpoint's own
    `require_api_key` dependency has nothing to do with. 400 carries no such special meaning
    anywhere in this app's client, so a rejected Garmin login shows an inline form error
    instead of logging the athlete out of their own account."""
    try:
        login_with_credentials(
            payload.username, payload.password, settings.garmin_tokenstore_dir_for(athlete_id)
        )
    except GarminConnectTooManyRequestsError as e:
        raise HTTPException(
            status_code=429,
            detail="Garmin rate-limited this login attempt -- wait before retrying.",
        ) from e
    except GarminConnectAuthenticationError as e:
        if "MFA" in str(e):
            raise HTTPException(
                status_code=422,
                detail=(
                    "This account requires an MFA code, which isn't supported here -- run "
                    "`sync auth login` in a terminal instead."
                ),
            ) from e
        raise HTTPException(
            status_code=400, detail="Incorrect Garmin username or password."
        ) from e
    return GarminLoginOut(success=True)


@router.post("/settings/garmin/sync")
def post_garmin_sync(
    background_tasks: BackgroundTasks,
    athlete_id: Annotated[str, Depends(require_api_key)],
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
) -> JobTriggerOut:
    """The same sync_garmin_connect() call `sync import garmin-connect` (cli.py) and the daily
    worker both already make, just triggered from the Settings page. Runs in the background --
    poll GET /settings/jobs/latest?source=garmin_connect for progress; sync_garmin_connect
    already writes its own ingest_run row, no extra bookkeeping needed here."""

    def _run() -> None:
        with engine.connect() as bg_conn:
            sync_garmin_connect(
                bg_conn,
                settings.raw_archive_dir,
                settings.parquet_dir,
                settings.garmin_tokenstore_dir_for(athlete_id),
                athlete_id=athlete_id,
                rolling_window_days=settings.garmin_rolling_window_days,
                rate_limits=RateLimitSettings(
                    request_interval_s=settings.garmin_request_interval_s,
                    max_requests_per_hour=settings.garmin_max_requests_per_hour,
                ),
            )

    background_tasks.add_task(_run)
    return JobTriggerOut(triggered=True)


@router.post("/settings/rebuild")
def post_rebuild(
    background_tasks: BackgroundTasks,
    athlete_id: Annotated[str, Depends(require_api_key)],
) -> JobTriggerOut:
    """Same as `sync rebuild --tracked` (cli.py), triggered from the Settings page. Never
    destructive -- rebuild_database_tracked wipes only derived tables and replays them from the
    raw archive. Poll GET /settings/jobs/latest?source=rebuild for progress.

    Launched as a standalone `sync rebuild --tracked` subprocess rather than an in-process
    BackgroundTasks call -- a real rebuild on bercy once hung for hours sharing a connection with
    this same live multi-worker process's own request handling. A subprocess gets its own
    interpreter, its own SQLAlchemy engine/connections, and no shared threads or event loop with
    the workers serving ordinary traffic, so it can hold whatever long transaction it needs
    without any of that contention. `ingest_run` bookkeeping (what GET /settings/jobs/latest
    polls) still happens exactly as before, since the subprocess is running the same
    rebuild_database_tracked function, just out-of-process -- fire-and-forget here, no need to
    wait on or communicate with it."""

    def _run() -> None:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "perseverer.cli",
                "rebuild",
                "--tracked",
                "--athlete-id",
                athlete_id,
            ],
            stdin=subprocess.DEVNULL,
        )

    background_tasks.add_task(_run)
    return JobTriggerOut(triggered=True)


@router.post("/settings/import/bulk-export")
async def post_bulk_export_import(
    background_tasks: BackgroundTasks,
    athlete_id: Annotated[str, Depends(require_api_key)],
    kind: Annotated[Literal["garmin", "strava"], Form()],
    file: UploadFile,
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_settings),
) -> JobTriggerOut:
    """Web counterpart of `sync import garmin-export`/`sync import strava-export <path>` -- the
    same idempotent full-archive-rescan importers, just fed an uploaded .zip instead of a path
    already on disk. The upload is streamed to a per-upload-unique temp path (not the CLI's own
    fixed extract_root, which would collide across two concurrent web uploads) before the
    background import runs; poll GET /settings/jobs/latest?source=garmin_export|strava_export
    for progress -- both importers already write their own ingest_run row.

    Known limitation: a reverse proxy in front of this API may cap request body size below a
    large export's real size; that's outside this app's own config."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="only .zip uploads are supported")

    upload_id = uuid4().hex
    upload_dir = settings.data_dir / "tmp" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    zip_path = upload_dir / f"{upload_id}.zip"
    with zip_path.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    extract_root = settings.data_dir / "tmp" / f"{kind}_export_{upload_id}"

    def _run() -> None:
        with engine.connect() as bg_conn:
            if kind == "garmin":
                import_garmin_export(
                    bg_conn,
                    settings.raw_archive_dir,
                    settings.parquet_dir,
                    extract_root,
                    athlete_id=athlete_id,
                    path=zip_path,
                )
            else:
                import_strava_export(
                    bg_conn,
                    settings.raw_archive_dir,
                    settings.parquet_dir,
                    extract_root,
                    athlete_id=athlete_id,
                    path=zip_path,
                )

    background_tasks.add_task(_run)
    return JobTriggerOut(triggered=True)


@router.get("/settings/jobs/latest")
def get_latest_job(
    source: Annotated[
        Literal["garmin_connect", "rebuild", "garmin_export", "strava_export"], Query()
    ],
    athlete_id: Annotated[str, Depends(require_api_key)],
    conn: Connection = Depends(get_conn),
) -> JobStatusOut | None:
    """Generic status polling for sync-now/rebuild/bulk-import -- all three write to the same
    `ingest_run` table each existing sync/import entrypoint already uses, just parameterized by
    `source` instead of one status endpoint per action."""
    row = _latest_ingest_run(conn, athlete_id, source)
    if row is None:
        return None
    errors = json.loads(row.errors) if row.errors else []
    return JobStatusOut(
        source=row.source,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        items_seen=row.items_seen,
        items_new=row.items_new,
        error_count=len(errors),
        first_error=_first_error(row.errors),
    )
