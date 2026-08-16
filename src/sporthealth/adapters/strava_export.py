"""The strava_export adapter: historical backfill from Strava's "export your data" bulk
archive, imported offline with zero network calls -- mirrors garmin_export.py's shape (a
directory-or-zip, zero-network importer), not a new architecture.

Real shape confirmed against a real archive (Phase 8, ADR 0012; do not re-derive this from
memory or public docs -- it was wrong in ways that matter): a top-level `activities.csv` (one
row per activity, Strava's own numeric `Activity ID` as the stable join key -- **not** the
number embedded in the per-activity filename, which is a different internal id and only
happens to equal Activity ID for the small minority of *uncompressed* files) plus an
`activities/` directory holding, per row, either nothing (a manually logged activity with no
GPS/sensor file -- ~0.6% of rows in the sample archive) or one file in whatever format the
original upload was: `.fit`/`.fit.gz` (the majority -- literally the same Garmin FIT bytes
already reachable via `garmin_export`/`garmin_connect` when Garmin auto-uploads to Strava, so
this is exactly the cross-source duplicate case the merge engine (`merge/engine.py`, already
source-agnostic since Phase 1) needs to catch), `.gpx`/`.gpx.gz` (new `gpx/parser.py`), or
`.tcx.gz` (new `tcx/parser.py`).

Every original file's exact bytes are archived before any decompression/parsing (raw-first,
non-negotiable) -- a `.gz`-suffixed file gets its own `strava_export_gz` raw_object for the
literal vendor bytes, in addition to the decompressed content's own raw_object (archived by
whichever parser path handles it), so nothing about "verbatim" is lost to decompression. Every
CSV row is *also* independently archived in full, positionally (not just by name) so that
`activities.csv`'s own real quirk -- five column names appear twice, e.g. a rounded
"Distance" summary in km alongside a precise "Distance" detail in metres -- can't silently
collapse a field; Python's own dict-of-last-value-wins is only used for the handful of typed
fields this adapter actually materializes (`_STRAVA_SPORT_MAP`, distance/duration/elevation/
calories overlay), which happen to always want the *last* occurrence of every real duplicate
observed in the sample archive.

For GPX/TCX-sourced activities, `activities.csv`'s own totals (distance, duration, elevation
gain, calories) are overlaid onto the parser's bare-geometry output via `dataclasses.replace`,
since Strava's own numbers are already available and more complete than anything re-derivable
from a GPX/TCX stream, and `activities.csv`'s own `Activity Type` (mapped through
`_STRAVA_SPORT_MAP`, built by decoding real FIT files that are the *same underlying activity* as
a same-typed GPX/TCX row -- see ADR 0012 -- not guessed) supplies sport/sub_sport, since
GPX/TCX carry that far less reliably than Strava's own classification. `.fit`/`.fit.gz` files
are left untouched by this overlay -- they go through the existing `ingest_dispatch.
ingest_fit_bytes` exactly like every other FIT source, since FIT's own session-level fields are
already the project's ground truth.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection

from sporthealth.adapters.fit_folder import IngestRunSummary, ingest_canonical_batch
from sporthealth.archive import archive_raw_bytes
from sporthealth.db.schema import ingest_run
from sporthealth.fit.types import CanonicalActivity, CanonicalBatch, ParsedMetric
from sporthealth.fitness import refresh_fitness_rollup
from sporthealth.gpx.parser import parse_gpx
from sporthealth.ingest_dispatch import ingest_fit_bytes
from sporthealth.insights.engine import refresh_insights
from sporthealth.performance import refresh_vdot
from sporthealth.rollups import refresh_daily_and_period_rollups
from sporthealth.tcx.parser import parse_tcx

SOURCE_NAME = "strava_export"

# Strava's own `Activity Type` -> this project's (sport, sub_sport) vocabulary, which is
# actually the Garmin FIT SDK's own enum string set (fit/parser.py::parse_fit just uses
# session.get("sport")/("sub_sport") verbatim -- confirmed by reading that code, not assumed).
# Every entry here was derived by decoding a real FIT file of that exact Strava-CSV type from
# the same real archive (see ADR 0012) and reading its real sport/sub_sport, not guessed --
# this is what makes cross-source merge matching (which compares sport *family*, see
# merge/engine.py::sport_family) actually recognize a GPX/TCX-only Strava activity as the same
# real-world activity as its Garmin-FIT-sourced twin. "Workout" is Strava's own generic catch-
# all bucket with no single reliable Garmin equivalent (a real sample decoded to yoga, but nothing
# guarantees that generalizes) -- mapped to bare "training" with no sub_sport rather than
# asserting one that might not hold.
_STRAVA_SPORT_MAP: dict[str, tuple[str, str | None]] = {
    "Run": ("running", "generic"),
    "Walk": ("walking", "generic"),
    "Hike": ("hiking", "generic"),
    "Ride": ("cycling", "generic"),
    "Virtual Ride": ("cycling", "virtual_activity"),
    "Rowing": ("rowing", "indoor_rowing"),
    "Snowshoe": ("snowshoeing", "generic"),
    "Alpine Ski": ("alpine_skiing", "generic"),
    "Rock Climb": ("rock_climbing", "bouldering"),
    "Weight Training": ("training", "strength_training"),
    "Elliptical": ("fitness_equipment", "elliptical"),
    "Yoga": ("training", "yoga"),
    "Workout": ("training", None),
}

# A bounded, deliberately small subset of activities.csv's ~90 columns materialized as typed
# metrics -- everything else stays reachable only via the full raw-archived row (satisfying
# "never drop a field" without registering ~90 rarely-queried metric_definition rows). Picked
# for being immediately useful and not already duplicated by a stream/core-field value.
#
# The three `strava.session.*` keys are a source-honest alternative to `fit.session.*` (which is
# only ever emitted by the FIT parser) for the same logical fields -- GPX/TCX-sourced activities
# have no session-level HR/descent field of their own to read, but activities.csv's own
# Average/Max Heart Rate and Elevation Loss columns are real vendor-supplied summary data, not
# derived here. The three read sites that need these values (routers/activities.py,
# insights/engine.py, ActivityStatsGrid.tsx) coalesce both key namespaces -- see ADR 0013.
_CSV_EXTRA_METRIC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Relative Effort", "strava.relative_effort"),
    ("Perceived Exertion", "strava.perceived_exertion"),
    ("Training Load", "strava.training_load"),
    ("Average Heart Rate", "strava.session.avg_heart_rate"),
    ("Max Heart Rate", "strava.session.max_heart_rate"),
    ("Elevation Loss", "strava.session.total_descent"),
)


def _map_sport(activity_type: str) -> tuple[str, str | None]:
    return _STRAVA_SPORT_MAP.get(activity_type, (activity_type.lower().replace(" ", "_"), None))


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_activity_date(text: str) -> datetime | None:
    # Strava's own CSV format, confirmed against a real archive: "Aug 2, 2026, 11:00:13 PM".
    # Only used as a start_time_utc fallback for the small minority of rows with no backing
    # file (a manually logged activity) -- every file-backed row uses the parsed file's own
    # embedded UTC timestamp instead, which is unambiguous. This text field's timezone is not
    # documented by Strava and wasn't independently verifiable for the manual-entry subset, so
    # it's treated as naive-implicitly-UTC per this project's datetime convention -- a real,
    # small, called-out limitation (see ADR 0012), not a silent assumption.
    try:
        return datetime.strptime(text, "%b %d, %Y, %I:%M:%S %p")
    except ValueError:
        return None


def _csv_extra_metrics(row: dict[str, str]) -> list[ParsedMetric]:
    metrics = []
    for column, metric_key in _CSV_EXTRA_METRIC_COLUMNS:
        val = _to_float(row.get(column))
        if val is not None:
            metrics.append(ParsedMetric(key=metric_key, value_num=val, unit=None))
    return metrics


def _overlay_csv_totals(activity: CanonicalActivity, row: dict[str, str]) -> CanonicalActivity:
    sport, sub_sport = _map_sport(row.get("Activity Type", ""))
    return replace(
        activity,
        sport=sport,
        sub_sport=sub_sport,
        name=activity.name or (row.get("Activity Name") or None),
        distance_m=_to_float(row.get("Distance")),
        duration_s=_to_float(row.get("Elapsed Time")) or activity.duration_s,
        moving_duration_s=_to_float(row.get("Moving Time")),
        elevation_gain_m=_to_float(row.get("Elevation Gain")),
        calories=_to_float(row.get("Calories")),
        extra_metrics=[*activity.extra_metrics, *_csv_extra_metrics(row)],
    )


def _activity_from_csv_only(row: dict[str, str]) -> CanonicalActivity | None:
    start = _parse_activity_date(row.get("Activity Date", ""))
    if start is None:
        return None
    sport, sub_sport = _map_sport(row.get("Activity Type", ""))
    return CanonicalActivity(
        start_time_utc=start,
        utc_offset_s=0,
        sport=sport,
        sub_sport=sub_sport,
        name=row.get("Activity Name") or None,
        duration_s=_to_float(row.get("Elapsed Time")),
        moving_duration_s=_to_float(row.get("Moving Time")),
        distance_m=_to_float(row.get("Distance")),
        elevation_gain_m=_to_float(row.get("Elevation Gain")),
        calories=_to_float(row.get("Calories")),
        device=None,
        extra_metrics=_csv_extra_metrics(row),
    )


def _read_activities_csv(csv_path: Path) -> list[tuple[str, dict[str, str], bytes]]:
    """Returns (activity_id, row_dict, raw_row_json_bytes) per row. `row_dict` is built via
    plain `dict(zip(header, values))` -- for the five real duplicate-named columns, this keeps
    the *last* occurrence, which matched the more precise/detailed value for every duplicate
    observed in the sample archive (see module docstring). `raw_row_json_bytes` preserves the
    row positionally (a list of [header, value] pairs, not a dict) so the duplicate columns
    survive verbatim in the raw archive regardless of what the typed extraction above uses.
    """
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        out = []
        for values in reader:
            row_dict = dict(zip(header, values, strict=False))
            activity_id = row_dict.get("Activity ID", "")
            raw_json = json.dumps(list(zip(header, values, strict=False))).encode("utf-8")
            out.append((activity_id, row_dict, raw_json))
        return out


def _extract_if_zip(path: Path, extract_root: Path) -> Path:
    if path.is_dir():
        return path
    if path.suffix.lower() != ".zip":
        raise ValueError(f"{path} is neither a directory nor a .zip file")
    import zipfile

    destination = extract_root / path.stem
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as zf:
        zf.extractall(destination)
    return destination


def import_strava_export(
    conn: Connection,
    archive_root: Path,
    parquet_dir: Path,
    extract_root: Path,
    *,
    athlete_id: str,
    path: Path,
) -> IngestRunSummary:
    """Imports every activity in `path` (a directory, or a `.zip` extracted into
    `extract_root` first). Always processes the full `activities.csv` -- there's no watermark
    for a one-off archive; re-running over an overlapping/updated export is a clean no-op via
    the same (athlete_id, source, external_id) idempotency key every adapter relies on, keyed
    here on Strava's own stable `Activity ID`.
    """
    root = _extract_if_zip(path, extract_root)
    csv_path = root / "activities.csv"
    if not csv_path.is_file():
        raise ValueError(f"{root} has no activities.csv -- not a Strava export archive")

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

    for activity_id, row, raw_row_json in _read_activities_csv(csv_path):
        if not activity_id:
            continue
        summary.items_seen += 1
        try:
            archive_raw_bytes(
                conn,
                archive_root,
                athlete_id=athlete_id,
                source=SOURCE_NAME,
                kind="strava_export_csv_row",
                content=raw_row_json,
                locator=f"activities.csv#id={activity_id}",
                external_id=activity_id,
            )

            filename = row.get("Filename", "")
            if filename:
                created, local_dates = _ingest_activity_file(
                    conn,
                    archive_root,
                    parquet_dir,
                    root,
                    row,
                    athlete_id=athlete_id,
                    activity_id=activity_id,
                    filename=filename,
                )
            else:
                created, local_dates = _ingest_manual_entry(
                    conn,
                    archive_root,
                    row,
                    raw_row_json,
                    athlete_id=athlete_id,
                    activity_id=activity_id,
                )

            touched_dates |= local_dates
            if created:
                summary.items_new += 1
            conn.commit()
        except Exception as e:  # one bad row must not abort the whole backfill
            conn.rollback()
            summary.errors.append({"activity_id": activity_id, "error": str(e)})

    refresh_daily_and_period_rollups(conn, athlete_id=athlete_id, touched_dates=touched_dates)
    if touched_dates:
        refresh_fitness_rollup(conn, athlete_id=athlete_id)
        refresh_insights(conn, athlete_id=athlete_id)
        refresh_vdot(conn, parquet_dir, athlete_id=athlete_id)
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


def csv_row_from_raw_json(content: bytes) -> dict[str, str]:
    """Reverses `_read_activities_csv`'s positional archival format (a JSON list of
    `[header, value]` pairs) back into the same row dict `_overlay_csv_totals`/
    `_activity_from_csv_only` expect. Used by `rebuild.py` to replay `strava_export_gpx`/
    `strava_export_tcx`/`strava_export_manual_entry` raw objects from the archive alone --
    raw-first means the replay must not need `activities.csv` back on disk.
    """
    pairs = json.loads(content.decode("utf-8"))
    return dict(pairs)


def ingest_manual_entry_content(
    conn: Connection,
    content: bytes,
    *,
    row: dict[str, str],
    athlete_id: str,
    activity_id: str,
    raw_id: int,
) -> tuple[bool, set[str]]:
    """The parse+ingest half of a manual-entry (no backing file) CSV row -- shared between the
    live import path (`_ingest_manual_entry`, which archives first) and `rebuild.py`'s replay
    path (which already has `content`/`raw_id` from a previously-archived raw object)."""
    activity = _activity_from_csv_only(row)
    if activity is None:
        return False, set()
    sha256 = hashlib.sha256(content).hexdigest()
    batch = CanonicalBatch(kind="activity", activity=activity)
    ingest_result = ingest_canonical_batch(
        conn,
        Path("."),  # unused: no stream to write for a file-less manual entry
        athlete_id=athlete_id,
        source=SOURCE_NAME,
        raw_object_id=raw_id,
        sha256=sha256,
        batch=batch,
        external_id_hint=activity_id,
    )
    dates = {ingest_result.local_date} if ingest_result.local_date else set()
    return ingest_result.created, dates


def _ingest_manual_entry(
    conn: Connection,
    archive_root: Path,
    row: dict[str, str],
    raw_row_json: bytes,
    *,
    athlete_id: str,
    activity_id: str,
) -> tuple[bool, set[str]]:
    raw_id = archive_raw_bytes(
        conn,
        archive_root,
        athlete_id=athlete_id,
        source=SOURCE_NAME,
        kind="strava_export_manual_entry",
        content=raw_row_json,
        locator=f"activities.csv#id={activity_id}",
        external_id=activity_id,
    )
    return ingest_manual_entry_content(
        conn, raw_row_json, row=row, athlete_id=athlete_id, activity_id=activity_id, raw_id=raw_id
    )


def _ingest_activity_file(
    conn: Connection,
    archive_root: Path,
    parquet_dir: Path,
    root: Path,
    row: dict[str, str],
    *,
    athlete_id: str,
    activity_id: str,
    filename: str,
) -> tuple[bool, set[str]]:
    file_path = root / filename
    raw_content = file_path.read_bytes()
    locator = str(file_path)

    if filename.lower().endswith(".gz"):
        # The literal vendor bytes, archived verbatim before any decompression (raw-first) --
        # the decompressed content gets its own, separate raw_object below/inside the FIT
        # dispatch, so nothing about "every byte fetched" is lost to unzipping in memory.
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="strava_export_gz",
            content=raw_content,
            locator=locator,
            external_id=activity_id,
        )
        content = gzip.decompress(raw_content)
        inner_suffix = Path(filename[: -len(".gz")]).suffix.lower()
    else:
        content = raw_content
        inner_suffix = Path(filename).suffix.lower()

    if inner_suffix == ".fit":
        dispatch_result = ingest_fit_bytes(
            conn,
            archive_root,
            parquet_dir,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            content=content,
            locator=locator,
            external_id_hint=activity_id,
        )
        created = dispatch_result.created
        dates = dispatch_result.affected_local_dates()
        return created, dates

    if inner_suffix == ".gpx":
        batch = parse_gpx(content)
        kind = "strava_export_gpx"
    elif inner_suffix == ".tcx":
        batch = parse_tcx(content)
        kind = "strava_export_tcx"
    else:
        archive_raw_bytes(
            conn,
            archive_root,
            athlete_id=athlete_id,
            source=SOURCE_NAME,
            kind="strava_export_other",
            content=content,
            locator=locator,
            external_id=activity_id,
        )
        return False, set()

    raw_id = archive_raw_bytes(
        conn,
        archive_root,
        athlete_id=athlete_id,
        source=SOURCE_NAME,
        kind=kind,
        content=content,
        locator=locator,
        external_id=activity_id,
    )
    return ingest_geometry_content(
        conn,
        parquet_dir,
        content,
        batch=batch,
        row=row,
        athlete_id=athlete_id,
        activity_id=activity_id,
        raw_id=raw_id,
    )


def ingest_geometry_content(
    conn: Connection,
    parquet_dir: Path,
    content: bytes,
    *,
    batch: CanonicalBatch,
    row: dict[str, str],
    athlete_id: str,
    activity_id: str,
    raw_id: int,
) -> tuple[bool, set[str]]:
    """The overlay+ingest half of a GPX/TCX-backed row -- shared between the live import path
    (`_ingest_activity_file`, which parses+archives first) and `rebuild.py`'s replay path
    (which already has a parsed `batch`/`content`/`raw_id` from a previously-archived raw
    object and doesn't re-read the file from disk)."""
    if batch.kind != "activity" or batch.activity is None:
        return False, set()

    overlaid = _overlay_csv_totals(batch.activity, row)
    sha256 = hashlib.sha256(content).hexdigest()
    ingest_result = ingest_canonical_batch(
        conn,
        parquet_dir,
        athlete_id=athlete_id,
        source=SOURCE_NAME,
        raw_object_id=raw_id,
        sha256=sha256,
        batch=replace(batch, activity=overlaid),
        external_id_hint=activity_id,
    )
    dates = {ingest_result.local_date} if ingest_result.local_date else set()
    return ingest_result.created, dates
