"""`sync` CLI — the operator-facing entrypoint for ingestion.

Registered as a console script (`uv run sync ...`) — see pyproject.toml's [project.scripts].
"""

from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

# Windows' default console codepage (cp1252) can't encode an emoji in e.g. a Garmin-sourced
# activity name ("☀️ San Jose Half Marathon 2026") -- confirmed the hard way: `sync
# correct-garmin-activities` crashed mid-report on a real one, after its own DB commit had
# already succeeded, losing nothing but the printed summary. utf-8 with errors="replace" makes
# every `typer.echo` call safe regardless of what a vendor's own text field contains, rather
# than every future command needing to know this about its own output.
if sys.stdout.encoding is not None and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
if sys.stderr.encoding is not None and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
from sqlalchemy import func, select
from ulid import ULID

from perseverer.adapters.apple_health_export import (
    detect_weight_cutoff_from_eufy,
    import_apple_health_export,
)
from perseverer.adapters.eufy import resolve_eufy_credentials, sync_eufy
from perseverer.adapters.fit_folder import import_from_folder
from perseverer.adapters.garmin_connect import (
    RateLimitSettings,
    login_with_credentials,
    sync_garmin_connect,
    token_store_status,
)
from perseverer.adapters.garmin_export import import_garmin_export
from perseverer.adapters.strava_export import import_strava_export
from perseverer.auth.api_keys import generate_api_key, hash_api_key
from perseverer.auth.passwords import hash_password
from perseverer.backfill_lap_moving_duration import backfill_lap_moving_duration
from perseverer.backfill_locations import backfill_locations
from perseverer.backfill_workouts import backfill_workouts
from perseverer.backup import create_backup, restore_backup
from perseverer.config import get_settings
from perseverer.db.engine import make_engine
from perseverer.db.schema import activity, activity_source_link, athlete_eufy_config
from perseverer.db.schema import athlete as athlete_table
from perseverer.db.seed import DEFAULT_ATHLETE_ID
from perseverer.gap import refresh_avg_gap
from perseverer.garmin_activity_summary import backfill_activity_corrections
from perseverer.garmin_connect_activity_name import backfill_garmin_activity_names
from perseverer.insights.engine import refresh_insights
from perseverer.pace_bands import refresh_pace_bands
from perseverer.performance import refresh_vdot
from perseverer.rebuild import rebuild_database_tracked, rebuild_database_via_shadow
from perseverer.weather_titles import backfill_weather_titles
from perseverer.worker.main import run_daily_sync

app = typer.Typer(help="perseverer sync CLI")
import_app = typer.Typer(help="One-shot imports from a source")
watch_app = typer.Typer(help="Continuously poll a source on an interval")
auth_app = typer.Typer(help="Garmin Connect authentication")
report_app = typer.Typer(help="Reports over the ingested data")
athlete_app = typer.Typer(help="Manage athlete login credentials (Phase 5 -- see ADR 0008)")
backup_app = typer.Typer(help="Backup + restore automation (Phase 9 -- see ADR 0014)")
app.add_typer(import_app, name="import")
app.add_typer(watch_app, name="watch")
app.add_typer(auth_app, name="auth")
app.add_typer(report_app, name="report")
app.add_typer(athlete_app, name="athlete")
app.add_typer(backup_app, name="backup")


FolderArg = Annotated[
    Path, typer.Argument(exists=True, file_okay=False, help="Directory of .fit files")
]

# Every ingestion/auth command defaults to DEFAULT_ATHLETE_ID like before this option existed --
# overridable for a second (or later) athlete's own sync, mirroring `sync rebuild`'s own
# pre-existing --athlete-id option.
AthleteIdOpt = Annotated[
    str, typer.Option("--athlete-id", help="Defaults to DEFAULT_ATHLETE_ID (the original athlete)")
]


@import_app.command("fit-folder")
def import_fit_folder(folder: FolderArg) -> None:
    """One-shot import of every .fit file in FOLDER (recognized by extension, not filename)."""
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        summary = import_from_folder(
            conn,
            settings.raw_archive_dir,
            settings.parquet_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
            folder=folder,
        )
    typer.echo(f"seen={summary.items_seen} new={summary.items_new} errors={len(summary.errors)}")
    if summary.errors:
        for e in summary.errors:
            typer.echo(f"  {e['file']}: {e['error']}", err=True)
        raise typer.Exit(code=1)


@watch_app.command("fit-folder")
def watch_fit_folder(
    folder: FolderArg,
    interval: Annotated[int, typer.Option(help="Seconds between polls")] = 30,
) -> None:
    """Continuously poll FOLDER every INTERVAL seconds. Polls, not inotify - see CLAUDE.md."""
    settings = get_settings()
    engine = make_engine(settings.db_path)
    typer.echo(f"Watching {folder} every {interval}s (Ctrl+C to stop)")
    while True:
        with engine.connect() as conn:
            summary = import_from_folder(
                conn,
                settings.raw_archive_dir,
                settings.parquet_dir,
                athlete_id=DEFAULT_ATHLETE_ID,
                folder=folder,
            )
        if summary.items_new or summary.errors:
            typer.echo(
                f"seen={summary.items_seen} new={summary.items_new} errors={len(summary.errors)}"
            )
        time.sleep(interval)


@import_app.command("garmin-export")
def import_garmin_export_cmd(
    path: Annotated[
        Path, typer.Argument(exists=True, help="Directory or .zip of a Garmin export archive")
    ],
    athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID,
) -> None:
    """One-shot backfill from a Garmin "Export Your Data" archive. Zero network calls."""
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        summary = import_garmin_export(
            conn,
            settings.raw_archive_dir,
            settings.parquet_dir,
            settings.data_dir / "tmp" / "garmin_export",
            athlete_id=athlete_id,
            path=path,
        )
    typer.echo(f"seen={summary.items_seen} new={summary.items_new} errors={len(summary.errors)}")
    if summary.errors:
        for e in summary.errors:
            typer.echo(f"  {e['file']}: {e['error']}", err=True)
        raise typer.Exit(code=1)


@import_app.command("strava-export")
def import_strava_export_cmd(
    path: Annotated[
        Path, typer.Argument(exists=True, help="Directory or .zip of a Strava export archive")
    ],
    athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID,
) -> None:
    """One-shot backfill from Strava's "export your data" archive. Zero network calls."""
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        summary = import_strava_export(
            conn,
            settings.raw_archive_dir,
            settings.parquet_dir,
            settings.data_dir / "tmp" / "strava_export",
            athlete_id=athlete_id,
            path=path,
        )
    typer.echo(f"seen={summary.items_seen} new={summary.items_new} errors={len(summary.errors)}")
    if summary.errors:
        for e in summary.errors:
            typer.echo(f"  {e['activity_id']}: {e['error']}", err=True)
        raise typer.Exit(code=1)


@import_app.command("apple-health")
def import_apple_health_cmd(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            help="A .zip, extracted directory, or export.xml from an Apple Health export",
        ),
    ],
    weight_before: Annotated[
        str | None,
        typer.Option(
            help="ISO date -- only import BodyMass/BMI/body-fat records before this date. "
            "Defaults to the athlete's earliest eufy.scale.weight reading."
        ),
    ] = None,
) -> None:
    """One-shot backfill of blood pressure (full history) and pre-Eufy body composition from an
    Apple Health "export.xml" archive. Zero network calls."""
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        cutoff = weight_before or detect_weight_cutoff_from_eufy(conn, DEFAULT_ATHLETE_ID)
        if cutoff is None:
            typer.echo(
                "No eufy.scale.weight data found and --weight-before not given -- skipping "
                "weight/BMI/body-fat import (blood pressure will still be imported).",
                err=True,
            )
        summary = import_apple_health_export(
            conn,
            settings.raw_archive_dir,
            settings.parquet_dir,
            settings.data_dir / "tmp" / "apple_health_export",
            athlete_id=DEFAULT_ATHLETE_ID,
            path=path,
            weight_before=cutoff,
        )
    typer.echo(f"seen={summary.items_seen} new={summary.items_new} errors={len(summary.errors)}")
    if summary.errors:
        for e in summary.errors:
            typer.echo(f"  {e['file']}: {e['error']}", err=True)
        raise typer.Exit(code=1)


@import_app.command("garmin-connect")
def import_garmin_connect_cmd(
    days: Annotated[
        int | None, typer.Option(help="Override the rolling re-fetch window in days")
    ] = None,
    athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID,
) -> None:
    """On-demand run of the same incremental sync the scheduler runs daily. Never falls back
    to a credentialed login - run `sync auth login` first if this fails with an auth error.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        summary = sync_garmin_connect(
            conn,
            settings.raw_archive_dir,
            settings.parquet_dir,
            settings.garmin_tokenstore_dir_for(athlete_id),
            athlete_id=athlete_id,
            rolling_window_days=days if days is not None else settings.garmin_rolling_window_days,
            rate_limits=RateLimitSettings(
                request_interval_s=settings.garmin_request_interval_s,
                max_requests_per_hour=settings.garmin_max_requests_per_hour,
            ),
        )
    typer.echo(f"seen={summary.items_seen} new={summary.items_new} errors={len(summary.errors)}")
    if summary.errors:
        for e in summary.errors:
            typer.echo(f"  {e}", err=True)
        raise typer.Exit(code=1)


@import_app.command("eufy")
def import_eufy_cmd(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """On-demand run of the Eufy body-composition sync. The API always returns the full
    reading history in one call, so there's no separate backfill-vs-incremental mode -- this
    is the same call the daily scheduler makes. Credentials come from `sync athlete
    set-eufy-credentials` for this athlete, falling back to the legacy global env vars only for
    DEFAULT_ATHLETE_ID (see adapters/eufy.py::resolve_eufy_credentials)."""
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        email, password, device_id, customer_id = resolve_eufy_credentials(
            conn,
            athlete_id,
            legacy_athlete_id=DEFAULT_ATHLETE_ID,
            legacy_email=settings.eufy_email,
            legacy_password=settings.eufy_password,
            legacy_device_id=settings.eufy_device_id,
            legacy_customer_id=settings.eufy_customer_id,
        )
        summary = sync_eufy(
            conn,
            settings.raw_archive_dir,
            settings.parquet_dir,
            athlete_id=athlete_id,
            email=email,
            password=password,
            device_id=device_id,
            customer_id=customer_id,
        )
    typer.echo(f"seen={summary.items_seen} new={summary.items_new} errors={len(summary.errors)}")
    if summary.errors:
        for e in summary.errors:
            typer.echo(f"  {e}", err=True)
        raise typer.Exit(code=1)


@app.command("daily-sync")
def daily_sync_cmd() -> None:
    """One shot of the exact same work the `worker` container's daily APScheduler job does
    (garmin_connect incremental sync + staleness check/webhook) -- see worker/main.py's own
    `run_daily_sync`. Exists so a platform that can't run the `worker` container (e.g. Windows,
    where Podman is known to corrupt this project's SQLite WAL file -- see CLAUDE.md) can still
    get the daily sync via a plain OS scheduler (Windows Task Scheduler, cron) invoking this
    command once a day instead.
    """
    run_daily_sync()


@auth_app.command("login")
def auth_login(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """Interactive Garmin login: prompts for email/password and, if required, an MFA code.
    Persists a token store so every future run resumes without this. The ONLY command in
    this project that ever authenticates with credentials - the scheduler and `sync import
    garmin-connect` only ever load this token store, never fall back to credentials.
    """
    settings = get_settings()
    tokenstore_dir = settings.garmin_tokenstore_dir_for(athlete_id)
    email = os.environ.get("GARMIN_EMAIL") or typer.prompt("Garmin email")
    password = os.environ.get("GARMIN_PASSWORD") or typer.prompt("Garmin password", hide_input=True)
    login_with_credentials(
        email,
        password,
        tokenstore_dir,
        prompt_mfa=lambda: typer.prompt("Garmin MFA code"),
    )
    typer.echo(f"Logged in. Token store saved to {tokenstore_dir}")


@auth_app.command("status")
def auth_status(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """Whether a Garmin token store exists and how long ago it was last written."""
    settings = get_settings()
    tokenstore_dir = settings.garmin_tokenstore_dir_for(athlete_id)
    present, age_days = token_store_status(tokenstore_dir)
    if not present:
        typer.echo("No token store found - run `sync auth login`.")
        raise typer.Exit(code=1)
    typer.echo(f"Token store present at {tokenstore_dir}, last written {age_days} day(s) ago.")


@athlete_app.command("create")
def athlete_create(
    display_name: Annotated[str, typer.Option(help="e.g. the athlete's first name")],
    timezone: Annotated[str, typer.Option(help="IANA name, e.g. America/Los_Angeles")] = "UTC",
    unit_preference: Annotated[str, typer.Option(help='"metric" or "imperial"')] = "metric",
) -> None:
    """Create a new athlete row -- the one provisioning step every other `athlete`/CLI command
    here assumes already exists. Prints the new athlete's id, needed by every command below (and
    by --athlete-id elsewhere) to actually provision and sync that athlete. See docs/DEPLOY.md's
    "Provisioning a second athlete" section for the full sequence.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    new_athlete_id = str(ULID())
    with engine.connect() as conn:
        conn.execute(
            athlete_table.insert().values(
                id=new_athlete_id,
                display_name=display_name,
                timezone=timezone,
                unit_preference=unit_preference,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        conn.commit()
    typer.echo(f"Created athlete {display_name!r} with id {new_athlete_id}")
    typer.echo(f"Next: sync athlete set-password --athlete-id {new_athlete_id}")


@athlete_app.command("set-password")
def athlete_set_password(
    athlete_id: Annotated[str, typer.Option(help="Athlete id to update")] = DEFAULT_ATHLETE_ID,
) -> None:
    """Set or change an athlete's login username/password. Run by a human, interactively --
    there is no self-service signup UI (see docs/adr/0008-phase-5-frontend.md).
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    username = typer.prompt("Username")
    password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    with engine.connect() as conn:
        result = conn.execute(
            athlete_table.update()
            .where(athlete_table.c.id == athlete_id)
            .values(username=username, password_hash=hash_password(password))
        )
        conn.commit()
    if result.rowcount == 0:
        typer.echo(f"No athlete with id {athlete_id}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Password set for athlete {athlete_id} (username={username})")


@athlete_app.command("create-key")
def athlete_create_key(
    athlete_id: Annotated[str, typer.Option(help="Athlete id to update")] = DEFAULT_ATHLETE_ID,
) -> None:
    """Generate a new standing API key for an athlete. Printed once -- the raw key is never
    stored and can't be shown again; only its hash is kept (see auth/api_keys.py).
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    raw_key = generate_api_key()
    with engine.connect() as conn:
        result = conn.execute(
            athlete_table.update()
            .where(athlete_table.c.id == athlete_id)
            .values(
                api_key_hash=hash_api_key(raw_key),
                api_key_created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        conn.commit()
    if result.rowcount == 0:
        typer.echo(f"No athlete with id {athlete_id}", err=True)
        raise typer.Exit(code=1)
    typer.echo("API key created -- save it now, it will not be shown again:")
    typer.echo(raw_key)


@athlete_app.command("set-eufy-credentials")
def athlete_set_eufy_credentials(
    athlete_id: Annotated[str, typer.Option(help="Athlete id to configure")] = DEFAULT_ATHLETE_ID,
) -> None:
    """Set or change an athlete's own Eufy Life scale credentials (email/password/device id/
    customer id), stored in `athlete_eufy_config` -- see adapters/eufy.py::sync_eufy for what
    device_id/customer_id mean and how to find them (same values the legacy
    PERSEVERER_EUFY_DEVICE_ID/CUSTOMER_ID env vars held for the original athlete). Plaintext,
    same as those env vars -- this is a reversible vendor credential Eufy's own API needs, not a
    local login this app authenticates against itself.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    email = typer.prompt("Eufy account email")
    password = typer.prompt("Eufy account password", hide_input=True)
    device_id = typer.prompt("Eufy device id")
    customer_id = typer.prompt("Eufy customer id")
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.connect() as conn:
        existing = conn.execute(
            select(athlete_eufy_config.c.athlete_id).where(
                athlete_eufy_config.c.athlete_id == athlete_id
            )
        ).scalar_one_or_none()
        values = {
            "email": email,
            "password": password,
            "device_id": device_id,
            "customer_id": customer_id,
            "updated_at": now,
        }
        if existing is None:
            conn.execute(athlete_eufy_config.insert().values(athlete_id=athlete_id, **values))
        else:
            conn.execute(
                athlete_eufy_config.update()
                .where(athlete_eufy_config.c.athlete_id == athlete_id)
                .values(**values)
            )
        conn.commit()
    typer.echo(f"Eufy credentials set for athlete {athlete_id}")


@backup_app.command("create")
def backup_create(
    destination: Annotated[
        str | None,
        typer.Option(
            help="Override the destination (local path or user@host:path) instead of"
            " PERSEVERER_BACKUP_HOST/USER/PATH -- mainly for an ad-hoc one-off backup, or a"
            " local-only test run (see the CI restore-from-backup job)."
        ),
    ] = None,
) -> None:
    """Snapshot the DB (VACUUM INTO) and rsync it plus the raw archive + Parquet trees to
    PERSEVERER_BACKUP_HOST/USER/PATH. This is what the worker's daily schedule also calls --
    runnable by hand for an on-demand backup. Skips (logs, exits 0) when unconfigured and no
    --destination override is given.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    result = create_backup(
        engine,
        settings.raw_archive_dir,
        settings.parquet_dir,
        settings.backups_dir,
        destination=destination,
        backup_user=settings.backup_user,
        backup_host=settings.backup_host,
        backup_path=settings.backup_path,
        ssh_key_path=settings.backup_ssh_key_path,
        known_hosts_path=str(settings.backup_known_hosts_path),
        keep_local_snapshots=settings.backup_keep_local_snapshots,
    )
    if result is None:
        typer.echo("Backup skipped: PERSEVERER_BACKUP_HOST/USER/PATH not configured.")
        return
    typer.echo(f"Backup complete: {result.snapshot_path.name} -> {result.destination}")


@backup_app.command("restore")
def backup_restore(
    source: Annotated[
        str,
        typer.Argument(
            help="Backup location to restore from -- a local path or a user@host:path rsync"
            " spec, matching whatever `sync backup create` was pointed at."
        ),
    ],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt (for scripted/CI use)."),
    ] = False,
) -> None:
    """Restore the DB + raw archive + Parquet trees from a backup, overwriting whatever is
    currently at this host's own data directory. CLI-only, deliberately not exposed via the
    Settings web UI -- this overwrites live data with an older snapshot, unlike the additive,
    non-destructive `rebuild` command (see backup.py's own module docstring). Human-initiated,
    one-shot, same as `sync auth login`.
    """
    settings = get_settings()
    if not yes and not typer.confirm(
        f"This will overwrite {settings.data_dir} with the backup at {source}. Continue?"
    ):
        raise typer.Exit(code=1)
    result = restore_backup(
        source,
        db_path=settings.db_path,
        raw_archive_dir=settings.raw_archive_dir,
        parquet_dir=settings.parquet_dir,
        ssh_key_path=settings.backup_ssh_key_path,
        known_hosts_path=str(settings.backup_known_hosts_path),
    )
    typer.echo(f"Restored {result.restored_db_path} from snapshot {result.source_snapshot}.")


@report_app.command("counts")
def report_counts() -> None:
    """Per-source activity counts, and how many activities exist in only one source."""
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        total_activities = conn.execute(select(func.count()).select_from(activity)).scalar_one()
        by_source = conn.execute(
            select(
                activity_source_link.c.source,
                func.count(func.distinct(activity_source_link.c.activity_id)),
            ).group_by(activity_source_link.c.source)
        ).fetchall()

        source_counts_per_activity = (
            select(
                activity_source_link.c.activity_id,
                func.count(func.distinct(activity_source_link.c.source)).label("n_sources"),
            )
            .group_by(activity_source_link.c.activity_id)
            .subquery()
        )
        unmatched = conn.execute(
            select(func.count())
            .select_from(source_counts_per_activity)
            .where(source_counts_per_activity.c.n_sources == 1)
        ).scalar_one()

    typer.echo(f"Total activities: {total_activities}")
    for source, count in by_source:
        typer.echo(f"  {source}: {count}")
    typer.echo(f"Activities present in only one source (unmatched): {unmatched}")


@app.command("rebuild")
def rebuild(
    tracked: Annotated[
        bool,
        typer.Option(
            "--tracked/--no-tracked",
            help=(
                "Write an ingest_run row (source=rebuild) the same way the Settings page's "
                "trigger polls for status, instead of just printing the result. Used internally "
                "when the API launches this as a standalone subprocess (see "
                "api/routers/settings.py::post_rebuild) -- a real production rebuild once hung "
                "for hours inside the live multi-worker api process, and this command run as its "
                "own OS process (not an in-process background task) is how that's now avoided; "
                "not meant for everyday interactive use."
            ),
        ),
    ] = False,
    athlete_id: Annotated[
        str,
        typer.Option(
            "--athlete-id",
            help=(
                "Defaults to DEFAULT_ATHLETE_ID like every other command here -- overridable so "
                "the API subprocess launch can pass through the specific athlete `require_api_key` "
                "actually resolved, rather than silently assuming the default one."
            ),
        ),
    ] = DEFAULT_ATHLETE_ID,
) -> None:
    """Replay the entire raw archive to reconstruct the database - proves "raw first": nothing
    here depends on any vendor being reachable. Replays into a throwaway shadow database first
    and only swaps it into the live tables once the replay fully succeeds (see
    rebuild_database_via_shadow's own docstring) -- this runs on the same box as
    perseverer-api/perseverer-worker in production, so the live tables must never be left
    wiped-and-partially-refilled for however long a replay takes, whether this command was
    triggered from here directly or via the Settings page.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        if tracked:
            replayed = rebuild_database_tracked(
                conn,
                settings.raw_archive_dir,
                settings.parquet_dir,
                settings.data_dir,
                athlete_id=athlete_id,
            )
        else:
            replayed = rebuild_database_via_shadow(
                conn,
                settings.raw_archive_dir,
                settings.parquet_dir,
                settings.data_dir,
                athlete_id=athlete_id,
            )
    typer.echo(f"Replayed {replayed} raw objects")


@app.command("refresh-insights")
def refresh_insights_cmd(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """Recomputes every insight from already-ingested data (no network call) -- for backfilling
    after this feature was added, or after adjusting a rule's thresholds. Every ingest entry
    point already calls this automatically; this command is for a manual, out-of-band refresh.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        count = refresh_insights(conn, athlete_id=athlete_id)
        conn.commit()
    typer.echo(f"computed {count} insights")


@app.command("correct-garmin-activities")
def correct_garmin_activities(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """Corrects activity.sport/name for already-ingested garmin_export activities using
    Garmin's own reclassification (summarizedActivitiesExport, already in the raw archive from
    a prior `import garmin-export` run -- no network call, no need to re-run the import). See
    garmin_activity_summary.py's own docstring for why this exists: a FIT file's recorded sport
    can simply be wrong (e.g. a hike recorded with the watch's "Run" profile still selected),
    and this export file is where the athlete's own after-the-fact correction actually lives.
    New `import garmin-export` runs apply this automatically; this command is for backfilling
    activities that were already ingested before this correction existed.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        result = backfill_activity_corrections(
            conn, settings.raw_archive_dir, athlete_id=athlete_id
        )
        conn.commit()
    typer.echo(
        f"corrected={result.corrected} unchanged={result.matched_no_change} "
        f"unmatched_entries={result.unmatched_entries}"
    )
    for activity_id, old_sport, new_sport, old_name, new_name in result.corrections:
        typer.echo(
            f"  {activity_id}: sport {old_sport!r}->{new_sport!r}, name {old_name!r}->{new_name!r}"
        )


@app.command("backfill-workouts")
def backfill_workouts_cmd(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """Backfills activity_workout/activity_workout_step for already-ingested activities from
    their already-archived raw FIT bytes -- no full `sync rebuild` needed. See
    backfill_workouts.py's own docstring for why: this is a purely additive parser change (a new
    message type this parser didn't model before), so a full rebuild's wipe-and-replay-everything
    is real overkill for it. New imports pick this up automatically going forward; this command
    is for backfilling activities ingested before workout parsing existed.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        count = backfill_workouts(conn, settings.raw_archive_dir, athlete_id=athlete_id)
        conn.commit()
    typer.echo(f"backfilled {count} activities with a workout plan")


@app.command("backfill-lap-moving-duration")
def backfill_lap_moving_duration_cmd(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """Backfills lap.moving_duration_s for already-ingested activities from their already-
    archived raw FIT bytes -- no full `sync rebuild` needed. See
    backfill_lap_moving_duration.py's own docstring: a lap's `duration_s` (total_elapsed_time)
    includes any device pause within that lap, while `moving_duration_s` (total_timer_time)
    doesn't -- a purely additive column this command backfills for laps ingested before it
    existed.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        count = backfill_lap_moving_duration(conn, settings.raw_archive_dir, athlete_id=athlete_id)
        conn.commit()
    typer.echo(f"backfilled moving_duration_s for {count} laps")


@app.command("backfill-locations")
def backfill_locations_cmd(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """Pre-warms geocoding.py's per-activity location cache for every already-ingested,
    GPS-bearing activity that doesn't have one yet. See backfill_locations.py's own docstring:
    this is what makes GET /activities/{id}/location answer from cache in normal use instead of
    hitting the background-fetch-on-miss path (added because the *first-ever* view of an
    activity was otherwise the slow one, blocked on Nominatim's own latency and this project's
    1-req/s throttle for it). Real network calls, respects that throttle, dedupes by rounded
    coordinate -- so on a large existing archive this can take a while; safe to interrupt and
    re-run, since already-cached activities are skipped.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        count = backfill_locations(conn, settings.raw_archive_dir, athlete_id=athlete_id)
    typer.echo(f"backfilled locations for {count} activities")


@app.command("backfill-vdot")
def backfill_vdot_cmd(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """Backfills perseverer.performance.vdot for already-ingested running activities -- no full
    `sync rebuild` needed. Unlike backfill-workouts/backfill-lap-moving-duration above, this
    doesn't even need to re-parse raw bytes: `refresh_vdot` reads only what's already in the
    database (activity.distance_m/moving_duration_s) and each activity's already-written Parquet
    stream, so this is a light, fast pass -- new ingests already compute this automatically going
    forward (see performance.py's own docstring); this command is for backfilling activities
    ingested before VDOT existed.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        count = refresh_vdot(conn, settings.parquet_dir, athlete_id=athlete_id)
        conn.commit()
    typer.echo(f"backfilled VDOT for {count} activities")


@app.command("backfill-pace-bands")
def backfill_pace_bands_cmd(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """Backfills perseverer.performance.pace_band.* for already-ingested running activities --
    no full `sync rebuild` needed. Like backfill-vdot above, reads only what's already in the
    database and each activity's already-written Parquet stream (this time the raw speed_mps
    samples, not just distance/duration); new ingests already compute this automatically going
    forward (see pace_bands.py's own docstring). This command is for backfilling activities
    ingested before pace bands existed.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        count = refresh_pace_bands(conn, settings.parquet_dir, athlete_id=athlete_id)
        conn.commit()
    typer.echo(f"backfilled pace bands for {count} activity/band rows")


@app.command("backfill-avg-gap")
def backfill_avg_gap_cmd(athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID) -> None:
    """Backfills perseverer.performance.avg_gap_speed_mps for already-ingested running
    activities -- no full `sync rebuild` needed. Like backfill-vdot/backfill-pace-bands above,
    reads only what's already in the database and each activity's already-written Parquet
    stream (this time the altitude_m and distance_m channels); new ingests already compute
    this automatically going forward (see gap.py's own docstring). This command is for
    backfilling activities ingested before average GAP existed.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        count = refresh_avg_gap(conn, settings.parquet_dir, athlete_id=athlete_id)
        conn.commit()
    typer.echo(f"backfilled average GAP for {count} activities")


@app.command("backfill-weather-titles")
def backfill_weather_titles_cmd(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change without writing anything."),
    ] = False,
    athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID,
) -> None:
    """Prepends a weather-condition emoji to every outdoor (GPS-bearing) activity's own title --
    e.g. "Morning Run" becomes "☀️ Morning Run". A title that already starts with an emoji (this
    command's own, or one already there) is left alone, which is what makes re-running this
    command safe and cheap: only genuinely new/never-touched activities do any work.

    Like backfill-vdot/backfill-pace-bands above, this now also runs automatically at the end of
    every ingest entry point's touched-dates block (see weather_titles.py's own docstring), so a
    newly-synced activity gets its emoji the same run it first appears. This command is for a
    one-off historical catch-up against an already-ingested archive (e.g. the very first run
    after this feature shipped, or after restoring from raw bytes) and for its own `--dry-run`
    preview -- not the only path that calls this anymore.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        changes = backfill_weather_titles(
            conn, settings.raw_archive_dir, athlete_id=athlete_id, dry_run=dry_run
        )
    verb = "would retitle" if dry_run else "retitled"
    typer.echo(f"{verb} {len(changes)} activities")
    for activity_id, old_title, new_title in changes:
        typer.echo(f"  {activity_id}: {old_title!r} -> {new_title!r}")


@app.command("backfill-garmin-activity-names")
def backfill_garmin_activity_names_cmd(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change without writing anything."),
    ] = False,
    athlete_id: AthleteIdOpt = DEFAULT_ATHLETE_ID,
) -> None:
    """Replaces a still-generic-default activity name (e.g. "Run") with Garmin Connect's own
    richer `activityName` (e.g. "Santa Clara - W12 Fri . [Consolidation] Easy"), already archived
    raw at ingest time -- no network call needed. An activity with a real custom title (or one
    already replaced by this command) is left alone; see garmin_connect_activity_name.py's own
    docstring for the exact narrow condition and why it's safe to re-run.

    Like backfill-weather-titles above, this now also runs automatically at the end of every
    garmin_connect sync's touched-dates block, before the weather emoji is applied. This command
    is for a one-off historical catch-up against an already-ingested archive and for its own
    `--dry-run` preview.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        changes = backfill_garmin_activity_names(
            conn, settings.raw_archive_dir, athlete_id=athlete_id, dry_run=dry_run
        )
    verb = "would retitle" if dry_run else "retitled"
    typer.echo(f"{verb} {len(changes)} activities")
    for activity_id, old_title, new_title in changes:
        typer.echo(f"  {activity_id}: {old_title!r} -> {new_title!r}")


if __name__ == "__main__":
    app()
