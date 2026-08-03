"""`sync` CLI — the operator-facing entrypoint for ingestion.

Registered as a console script (`uv run sync ...`) — see pyproject.toml's [project.scripts].
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

import typer

from sporthealth.adapters.fit_folder import import_from_folder
from sporthealth.config import get_settings
from sporthealth.db.engine import make_engine
from sporthealth.db.seed import DEFAULT_ATHLETE_ID
from sporthealth.rebuild import rebuild_database

app = typer.Typer(help="sporthealth sync CLI")
import_app = typer.Typer(help="One-shot imports from a source")
watch_app = typer.Typer(help="Continuously poll a source on an interval")
app.add_typer(import_app, name="import")
app.add_typer(watch_app, name="watch")


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
    """Continuously poll FOLDER every INTERVAL seconds. Polls, not inotify — see CLAUDE.md."""
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
