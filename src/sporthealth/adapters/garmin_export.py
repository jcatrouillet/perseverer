"""The garmin_export adapter: the historical backfill from Garmin's "Export Your Data" GDPR
archive, imported offline with zero network calls (§6 of the project spec).

FIT files anywhere in the archive go through the same unified dispatch as `fit_folder`
(`ingest_dispatch.ingest_fit_bytes`, activity-or-health) — this adapter is a thin
discovery/dispatch layer, not a new parser. Every other file in the archive (the wellness/
health JSON, CSVs, etc.) is archived raw for completeness (raw-first holds regardless), but
not structurally parsed yet — Garmin's export JSON schema is undocumented and unsampled as of
this writing; see docs/adr/0003-phase-2-garmin-adapters.md for why guessing it blind was
rejected. (`fit_folder`'s narrow daily_summary/hydration JSON recognition is deliberately not
extended here — see docs/adr/0004-phase-2-health-ingestion.md.)
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
from sporthealth.ingest_dispatch import ingest_fit_bytes

SOURCE_NAME = "garmin_export"

# Garmin's own export naming, confirmed from real filenames in a Garmin export archive (see
# Phase 1's test data, e.g. "10009743933_ACTIVITY.fit") — the digits are Garmin's own stable
# activityId, a better external_id than fit_folder's device+timestamp fallback.
_ACTIVITY_FILENAME_RE = re.compile(r"^(\d+)_ACTIVITY\.fit$", re.IGNORECASE)


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
    for file_path in sorted(p for p in root.rglob("*") if p.is_file()):
        summary.items_seen += 1
        try:
            content = file_path.read_bytes()
            locator = str(file_path)

            if file_path.suffix.lower() == ".fit":
                external_id_hint = None
                match = _ACTIVITY_FILENAME_RE.match(file_path.name)
                if match:
                    external_id_hint = match.group(1)

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
                if dispatch_result.created:
                    summary.items_new += 1
            else:
                archive_raw_bytes(
                    conn,
                    archive_root,
                    athlete_id=athlete_id,
                    source=SOURCE_NAME,
                    kind=_kind_for_suffix(file_path.suffix),
                    content=content,
                    locator=locator,
                )

            conn.commit()
        except Exception as e:  # one bad file must not abort the whole backfill
            conn.rollback()
            summary.errors.append({"file": str(file_path), "error": str(e)})

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
