"""The apple_health_export adapter: a one-time, zero-network-call backfill from Apple Health's
"export.xml" archive (Settings > [Name] > Export All Health Data on iOS) -- mirrors
`adapters/eufy.py`'s shape for the archive-parse-ingest-rollup sequence (health-observation-only,
no activity data, so only the daily/period rollups need refreshing afterward) and
`adapters/garmin_export.py`'s shape for zip-handling, `ingest_run` tracking, and CLI reporting.

Archives the *entire* export.xml as a single raw_object (`kind="apple_health_export_xml"`) rather
than per-record -- no adapter in this codebase archives at sub-file granularity (one raw_object
per FIT file, per Garmin JSON report, per Eufy API reading; a *generic* parser then pulls many
observations out of that one archived blob), and a real export.xml can run to gigabytes with
millions of XML elements, so per-record archiving would mean millions of tiny raw_object rows for
a one-time import. See `health/apple_health_parser.py`'s own docstring for what's extracted from
it today (blood pressure, full history; body mass/BMI/body-fat percentage from before the
athlete's Eufy scale) and why everything else in the file stays unparsed-but-archived, exactly
like `garmin_export.py`'s own account-data/other-product-domain files.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, func, select

from perseverer.adapters.fit_folder import IngestRunSummary
from perseverer.archive import archive_raw_bytes
from perseverer.db.schema import health_observation, ingest_run
from perseverer.health.apple_health_parser import parse_apple_health_export_xml
from perseverer.health.ingest import ingest_health_batch
from perseverer.rollups import refresh_daily_and_period_rollups

SOURCE_NAME = "apple_health_export"

_EUFY_WEIGHT_METRIC_KEY = "eufy.scale.weight"


def detect_weight_cutoff_from_eufy(conn: Connection, athlete_id: str) -> str | None:
    """The earliest eufy.scale.weight observation's local date, or None if no Eufy data exists
    yet. A CLI-only convenience -- import_apple_health_export itself takes an explicit
    weight_before so it stays a pure, deterministic function of its inputs (no hidden DB read,
    easier to test and to replay identically from rebuild.py).
    """
    earliest = conn.execute(
        select(func.min(health_observation.c.observed_at_utc)).where(
            health_observation.c.athlete_id == athlete_id,
            health_observation.c.metric_key == _EUFY_WEIGHT_METRIC_KEY,
        )
    ).scalar_one_or_none()
    if earliest is None:
        return None
    observed_at_utc = (
        earliest if isinstance(earliest, datetime) else datetime.fromisoformat(earliest)
    )
    return observed_at_utc.date().isoformat()


def _extract_if_zip(path: Path, extract_root: Path) -> Path:
    if path.is_dir():
        return path
    if path.suffix.lower() != ".zip":
        raise ValueError(f"{path} is neither a directory nor a .zip file")

    destination = extract_root / path.stem
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        zf.extractall(destination)
    return destination


def _locate_export_xml(path: Path, extract_root: Path) -> Path:
    """`path` may be a bare export.xml file, a directory, or a .zip -- the real Apple export puts
    export.xml one level deep, under `apple_health_export/`, so both layouts are checked before
    falling back to a full recursive search."""
    if path.is_file() and path.suffix.lower() == ".xml":
        return path

    root = _extract_if_zip(path, extract_root)
    direct = root / "export.xml"
    if direct.is_file():
        return direct
    nested = root / "apple_health_export" / "export.xml"
    if nested.is_file():
        return nested
    found = next(root.rglob("export.xml"), None)
    if found is None:
        raise ValueError(f"no export.xml found under {path}")
    return found


def import_apple_health_export(
    conn: Connection,
    archive_root: Path,
    parquet_dir: Path,
    extract_root: Path,
    *,
    athlete_id: str,
    path: Path,
    weight_before: str | None,
) -> IngestRunSummary:
    """Imports blood pressure (full history) and, if `weight_before` is given, body mass/BMI/
    body-fat percentage dated strictly before that ISO date -- see
    `health/apple_health_parser.py` for the exact scope and exclusions. Always re-processes the
    full file; re-running over the same export is a clean no-op via `archive_raw_bytes`' content
    addressing and `ingest_health_batch`'s own idempotent upsert.
    """
    xml_path = _locate_export_xml(path, extract_root)

    started_at = datetime.now(UTC)
    result = conn.execute(
        ingest_run.insert().values(
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            started_at=started_at,
            status="running",
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
        content = xml_path.read_bytes()
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="apple_health_export_xml",
            content=content,
            locator=str(xml_path),
        )
        batch = parse_apple_health_export_xml(content, weight_before=weight_before)
        health_result = ingest_health_batch(
            conn, parquet_dir, athlete_id=athlete_id, source=SOURCE_NAME, batch=batch
        )
        touched_dates |= health_result.affected_local_dates
        summary.items_seen = health_result.observations_seen
        summary.items_new = health_result.observations_new
        conn.commit()
    except Exception as e:  # one bad file must not corrupt the run's ingest_run bookkeeping
        conn.rollback()
        summary.errors.append({"file": str(xml_path), "error": str(e)})

    refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates=touched_dates)
    conn.commit()

    conn.execute(
        ingest_run.update()
        .where(ingest_run.c.id == run_id)
        .values(
            finished_at=datetime.now(UTC),
            status="failed" if summary.errors else "success",
            items_seen=summary.items_seen,
            items_new=summary.items_new,
        )
    )
    conn.commit()
    return summary
