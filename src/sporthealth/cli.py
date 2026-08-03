"""`sync` CLI — the operator-facing entrypoint for ingestion.

Registered as a console script (`uv run sync ...`) — see pyproject.toml's [project.scripts].
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from garminconnect import Garmin
from sqlalchemy import func, select

from sporthealth.adapters.fit_folder import import_from_folder
from sporthealth.adapters.garmin_connect import RateLimitSettings, sync_garmin_connect
from sporthealth.adapters.garmin_export import import_garmin_export
from sporthealth.config import get_settings
from sporthealth.db.engine import make_engine
from sporthealth.db.schema import activity, activity_source_link
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.rebuild import rebuild_database

app = typer.Typer(help="sporthealth sync CLI")
import_app = typer.Typer(help="One-shot imports from a source")
watch_app = typer.Typer(help="Continuously poll a source on an interval")
auth_app = typer.Typer(help="Garmin Connect authentication")
report_app = typer.Typer(help="Reports over the ingested data")
app.add_typer(import_app, name="import")
app.add_typer(watch_app, name="watch")
app.add_typer(auth_app, name="auth")
app.add_typer(report_app, name="report")


FolderArg = Annotated[
    Path, typer.Argument(exists=True, file_okay=False, help="Directory of .fit files")
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
            athlete_id=DEFAULT_ATHLETE_ID,
            path=path,
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
            settings.garmin_tokenstore_dir,
            athlete_id=DEFAULT_ATHLETE_ID,
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


@auth_app.command("login")
def auth_login() -> None:
    """Interactive Garmin login: prompts for email/password and, if required, an MFA code.
    Persists a token store so every future run resumes without this. The ONLY command in
    this project that ever authenticates with credentials - the scheduler and `sync import
    garmin-connect` only ever load this token store, never fall back to credentials.
    """
    settings = get_settings()
    email = os.environ.get("GARMIN_EMAIL") or typer.prompt("Garmin email")
    password = os.environ.get("GARMIN_PASSWORD") or typer.prompt(
        "Garmin password", hide_input=True
    )
    client = Garmin(email, password, prompt_mfa=lambda: typer.prompt("Garmin MFA code"))
    settings.garmin_tokenstore_dir.mkdir(parents=True, exist_ok=True)
    client.login(tokenstore=str(settings.garmin_tokenstore_dir))
    typer.echo(f"Logged in. Token store saved to {settings.garmin_tokenstore_dir}")


@auth_app.command("status")
def auth_status() -> None:
    """Whether a Garmin token store exists and how long ago it was last written."""
    settings = get_settings()
    tokendir = settings.garmin_tokenstore_dir
    if not tokendir.exists() or not any(tokendir.iterdir()):
        typer.echo("No token store found - run `sync auth login`.")
        raise typer.Exit(code=1)
    newest_mtime = max(f.stat().st_mtime for f in tokendir.rglob("*") if f.is_file())
    age_days = (datetime.now(UTC) - datetime.fromtimestamp(newest_mtime, tz=UTC)).days
    typer.echo(f"Token store present at {tokendir}, last written {age_days} day(s) ago.")


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
def rebuild() -> None:
    """Wipe every derived table and replay the entire raw archive to reconstruct the
    database - proves "raw first": nothing here depends on any vendor being reachable.
    """
    settings = get_settings()
    engine = make_engine(settings.db_path)
    with engine.connect() as conn:
        replayed = rebuild_database(
            conn, settings.raw_archive_dir, settings.parquet_dir, athlete_id=DEFAULT_ATHLETE_ID
        )
    typer.echo(f"Replayed {replayed} raw objects")


if __name__ == "__main__":
    app()
