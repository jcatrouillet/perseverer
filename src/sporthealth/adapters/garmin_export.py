"""The garmin_export adapter: the historical backfill from Garmin's "Export Your Data" GDPR
archive, imported offline with zero network calls (§6 of the project spec).

FIT files anywhere in the archive (including nested inside further .zip files -- confirmed
necessary against a real export, see docs/adr/0005-phase-2-garmin-export-real-data.md) go
through the same unified dispatch as `fit_folder` (`ingest_dispatch.ingest_fit_bytes`,
activity-or-health) -- this adapter is a thin discovery/dispatch layer, not a new parser.
Health JSON under DI-Connect-Wellness/Metrics/Aggregator goes through a generic,
report-kind-namespaced parser (`health.json_parser.parse_garmin_export_json`) -- see ADR 0005
for why that's generic rather than bespoke per report kind. Everything else (account data,
bulk activity summaries, and the many non-fitness product domains a Garmin account's export
can bundle) is archived raw for completeness (raw-first holds regardless), but not
structurally parsed.
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection

from sporthealth.adapters.fit_folder import IngestRunSummary
from sporthealth.archive import archive_raw_bytes
from sporthealth.db.schema import athlete, ingest_run
from sporthealth.fitness import refresh_fitness_rollup
from sporthealth.health.ingest import ingest_health_batch
from sporthealth.health.json_parser import parse_garmin_export_json
from sporthealth.ingest_dispatch import ingest_fit_bytes
from sporthealth.rollups import refresh_daily_and_period_rollups

SOURCE_NAME = "garmin_export"

# Garmin's own export naming, confirmed from real filenames in a Garmin export archive (see
# Phase 1's test data, e.g. "10009743933_ACTIVITY.fit") — the digits are Garmin's own stable
# activityId, a better external_id than fit_folder's device+timestamp fallback.
_ACTIVITY_FILENAME_RE = re.compile(r"^(\d+)_ACTIVITY\.fit$", re.IGNORECASE)

# The real GDPR export's own naming, confirmed against a real archive: "<email>_<id>.fit" --
# tried second, after _ACTIVITY_FILENAME_RE, since it's more permissive (see ADR 0005 decision
# 2). Files matching neither (e.g. the fixed-name device/training backup FIT files) fall back
# to ingest_fit_bytes's device-serial+start-time/sha256 heuristic.
_EXPORT_FIT_ID_RE = re.compile(r"_(\d+)\.fit$", re.IGNORECASE)

# Directories (by immediate parent name) confirmed to hold the GDPR export's own
# day/event-record-array health JSON shape -- see ADR 0005 decision 5. Everything else (account
# data, bulk activity summaries, unrelated product domains) stays on the raw-only path below.
_EXPORT_HEALTH_JSON_DIRS = frozenset(
    {"DI-Connect-Wellness", "DI-Connect-Metrics", "DI-Connect-Aggregator"}
)

# Strips a date-range token ("2023-01-12" or "20230112") or a long numeric profile id from a
# GDPR export filename's underscore-separated parts, leaving the report kind itself -- e.g.
# "2023-01-12_2023-04-22_87061520_sleepData.json" -> "sleepData",
# "UDSFile_2022-10-03_2023-01-11.json" -> "UDSFile". See ADR 0005 decision 3.
_DATE_TOKEN_RE = re.compile(r"^\d{4}-?\d{2}-?\d{2}$")
_PROFILE_ID_RE = re.compile(r"^\d{5,}$")

_MAX_ZIP_EXTRACT_DEPTH = 5


def report_kind_from_filename(name: str) -> str:
    """Public since Phase 6: also used by rebuild.py to replay garmin_export_health_json raw
    objects, deriving report_kind from the archived source_locator (the original filename)
    rather than re-deciding it.
    """
    stem = Path(name).stem
    parts = [p for p in stem.split("_") if p]
    kept = [p for p in parts if not _DATE_TOKEN_RE.match(p) and not _PROFILE_ID_RE.match(p)]
    return "_".join(kept) if kept else stem


def _derive_export_external_id(filename: str) -> str | None:
    match = _ACTIVITY_FILENAME_RE.match(filename)
    if match:
        return match.group(1)
    match = _EXPORT_FIT_ID_RE.search(filename)
    if match:
        return match.group(1)
    return None


def _is_export_health_json(file_path: Path, root: Path) -> bool:
    try:
        rel_parts = file_path.relative_to(root).parts
    except ValueError:
        return False
    return len(rel_parts) >= 2 and rel_parts[-2] in _EXPORT_HEALTH_JSON_DIRS


def _kind_for_suffix(suffix: str) -> str:
    suffix = suffix.lower().lstrip(".")
    if suffix == "json":
        return "garmin_export_json"
    if suffix == "csv":
        return "garmin_export_csv"
    return "garmin_export_other"


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


def _extract_nested_zips(root: Path) -> None:
    """Repeatedly finds every remaining *.zip under `root` and extracts each into a sibling
    directory, so FIT files nested inside further zips (confirmed real: a real export's
    DI-Connect-Uploaded-Files/UploadedFiles_*.zip, plus a couple of single-file backup zips --
    see ADR 0005) are reachable by the flat file walk below. Never deletes the nested zip
    itself (non-destructive; the main walk just skips .zip-suffix entries afterward). Real
    data nests exactly one level deep; the depth cap is defensive only.
    """
    processed: set[Path] = set()
    for _ in range(_MAX_ZIP_EXTRACT_DEPTH):
        nested = sorted(p for p in root.rglob("*.zip") if p.is_file() and p not in processed)
        if not nested:
            return
        for zpath in nested:
            destination = zpath.parent / f"{zpath.stem}__extracted"
            destination.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(destination)
            processed.add(zpath)
    raise RuntimeError(f"Zip nesting exceeded max depth {_MAX_ZIP_EXTRACT_DEPTH} under {root}")


def import_garmin_export(
    conn: Connection,
    archive_root: Path,
    parquet_dir: Path,
    extract_root: Path,
    *,
    athlete_id: str,
    path: Path,
) -> IngestRunSummary:
    """Imports every file found under `path` (a directory, or a `.zip` that gets extracted
    into `extract_root` first). Always processes the full tree — there's no "since" watermark
    for a one-off archive; re-running over an overlapping/updated export is a clean no-op via
    the same (athlete_id, sha256) / (athlete_id, source, external_id) idempotency keys
    `fit_folder` already relies on.
    """
    root = _extract_if_zip(path, extract_root)
    _extract_nested_zips(root)

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
    for file_path in sorted(p for p in root.rglob("*") if p.is_file()):
        suffix = file_path.suffix.lower()
        if suffix == ".zip":
            # Contents already extracted (_extract_nested_zips) and archived/parsed
            # individually -- archiving the container itself would just duplicate bytes.
            continue

        summary.items_seen += 1
        try:
            content = file_path.read_bytes()
            locator = str(file_path)

            if suffix == ".fit":
                external_id_hint = _derive_export_external_id(file_path.name)
                dispatch_result = ingest_fit_bytes(
                    conn,
                    archive_root,
                    parquet_dir,
                    athlete_id=athlete_id,
                    source=SOURCE_NAME,
                    content=content,
                    locator=locator,
                    external_id_hint=external_id_hint,
                )
                touched_dates |= dispatch_result.affected_local_dates()
                if dispatch_result.created:
                    summary.items_new += 1
            elif suffix == ".json" and _is_export_health_json(file_path, root):
                archive_raw_bytes(
                    conn,
                    archive_root,
                    athlete_id=athlete_id,
                    source=SOURCE_NAME,
                    kind="garmin_export_health_json",
                    content=content,
                    locator=locator,
                )
                report_kind = report_kind_from_filename(file_path.name)
                batch = parse_garmin_export_json(content, report_kind=report_kind)
                health_result = ingest_health_batch(
                    conn, parquet_dir, athlete_id=athlete_id, source=SOURCE_NAME, batch=batch
                )
                touched_dates |= health_result.affected_local_dates
                if health_result.observations_new > 0 or health_result.sleep_sessions_new > 0:
                    summary.items_new += 1
            else:
                archive_raw_bytes(
                    conn,
                    archive_root,
                    athlete_id=athlete_id,
                    source=SOURCE_NAME,
                    kind=_kind_for_suffix(suffix),
                    content=content,
                    locator=locator,
                )

            conn.commit()
        except Exception as e:  # one bad file must not abort the whole backfill
            conn.rollback()
            summary.errors.append({"file": str(file_path), "error": str(e)})

    refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates=touched_dates)
    if touched_dates:
        refresh_fitness_rollup(conn, athlete_id=athlete_id)
    conn.commit()

    conn.execute(
        athlete.update()
        .where(athlete.c.id == athlete_id)
        .values(last_full_export_at=datetime.now(UTC))
    )
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
