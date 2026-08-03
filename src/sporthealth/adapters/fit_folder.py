"""The fit_folder adapter: the "universal offline importer".

Used for the Garmin export archive, the Strava export ZIP's FIT/TCX files (later phases),
manual repair, and offline testing of every other adapter against real files without
touching the network. Polls a directory rather than relying on inotify — files arriving via
SMB/rsync/Drive don't reliably fire inotify events inside a container (see CLAUDE.md).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polyline as polyline_codec
from sqlalchemy import Connection, select
from ulid import ULID

from sporthealth.adapters.base import AdapterHealth, ObjectRef, RawPayload
from sporthealth.archive import archive_raw_bytes
from sporthealth.db.schema import (
    activity,
    activity_metric,
    activity_source_link,
    activity_stream,
    ingest_run,
    lap,
    merge_decision,
    route_geom,
)
from sporthealth.db.schema import (
    device as device_table,
)
from sporthealth.db.schema import (
    split as split_table,
)
from sporthealth.fit.parser import parse_fit
from sporthealth.fit.types import CanonicalBatch, ParsedDevice
from sporthealth.health.ingest import ingest_health_batch
from sporthealth.health.json_parser import parse_daily_summary_json, parse_hydration_json
from sporthealth.merge.engine import ActivityCandidate, is_same_activity
from sporthealth.metrics.registry import get_or_register_metric
from sporthealth.streams import write_activity_stream

_EPOCH = datetime.fromtimestamp(0, tz=UTC)
_MERGE_WINDOW = timedelta(days=1)

# fit_folder's charter is FIT files, plus these two specific Garmin Connect-shaped JSON
# filename patterns confirmed against real data (see docs/adr/0004-phase-2-health-ingestion.md)
# — not a general JSON importer.
_DAILY_SUMMARY_JSON_RE = re.compile(r"^daily_summary_\d{4}-\d{2}-\d{2}\.json$", re.IGNORECASE)
_HYDRATION_JSON_RE = re.compile(r"^hydration_\d{4}-\d{2}-\d{2}\.json$", re.IGNORECASE)


class FitFolderAdapter:
    name = "fit_folder"

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def health_check(self) -> AdapterHealth:
        if not self.folder.is_dir():
            return AdapterHealth(ok=False, detail=f"{self.folder} is not a directory")
        return AdapterHealth(ok=True)

    def authenticate(self) -> None:
        pass  # no credentials needed — it's a directory

    def list_changed(self, since: datetime) -> list[ObjectRef]:
        # Explicit suffix/filename checks (not path.glob("*.fit")): glob is case-insensitive
        # on Windows but case-sensitive on Linux, so "*.fit" alone would silently miss
        # ACTIVITY.FIT on the NAS. Content hashing (in fetch_raw/archive) is the real
        # idempotency mechanism — mtime here is only a cheap pre-filter, not a correctness
        # requirement.
        refs = []
        for path in sorted(self.folder.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            is_fit = suffix == ".fit"
            is_health_json = suffix == ".json" and (
                _DAILY_SUMMARY_JSON_RE.match(path.name) or _HYDRATION_JSON_RE.match(path.name)
            )
            if not (is_fit or is_health_json):
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if mtime >= since:
                refs.append(ObjectRef(locator=str(path)))
        return refs

    def fetch_raw(self, ref: ObjectRef) -> RawPayload:
        content = Path(ref.locator).read_bytes()
        return RawPayload(content=content, kind="fit_activity")

    def parse(self, raw_bytes: bytes) -> CanonicalBatch:
        return parse_fit(raw_bytes)


@dataclass(frozen=True)
class IngestResult:
    activity_id: str | None
    created: bool


@dataclass
class IngestRunSummary:
    run_id: int
    items_seen: int = 0
    items_new: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)


def _derive_external_id(start_time_utc: datetime, device: ParsedDevice | None, sha256: str) -> str:
    if device and device.serial_number:
        return f"{device.serial_number}:{start_time_utc.isoformat()}"
    return sha256


def _upsert_device(conn: Connection, athlete_id: str, device: ParsedDevice) -> int | None:
    if not device.serial_number and not device.product:
        return None
    existing = conn.execute(
        select(device_table.c.id).where(
            device_table.c.athlete_id == athlete_id,
            device_table.c.manufacturer == device.manufacturer,
            device_table.c.product == device.product,
            device_table.c.serial_number == device.serial_number,
        )
    ).scalar_one_or_none()
    if existing is not None:
        assert isinstance(existing, int)
        return existing
    result = conn.execute(
        device_table.insert().values(
            athlete_id=athlete_id,
            manufacturer=device.manufacturer,
            product=device.product,
            serial_number=device.serial_number,
            first_seen_at=datetime.now(UTC),
        )
    )
    assert result.inserted_primary_key is not None
    new_id = result.inserted_primary_key[0]
    assert isinstance(new_id, int)
    return new_id


def _find_merge_match(
    conn: Connection, athlete_id: str, candidate: ActivityCandidate
) -> str | None:
    window_start = candidate.start_time_utc - _MERGE_WINDOW
    window_end = candidate.start_time_utc + _MERGE_WINDOW
    rows = conn.execute(
        select(
            activity.c.id, activity.c.start_time_utc, activity.c.duration_s, activity.c.sport
        ).where(
            activity.c.athlete_id == athlete_id,
            activity.c.start_time_utc.between(window_start, window_end),
            activity.c.deleted_at.is_(None),
        )
    ).fetchall()

    for row in rows:
        existing = ActivityCandidate(
            start_time_utc=row.start_time_utc, duration_s=row.duration_s, sport=row.sport
        )
        decision = is_same_activity(candidate, existing)
        if decision.is_match:
            conn.execute(
                merge_decision.insert().values(
                    athlete_id=athlete_id,
                    matched_activity_id=row.id,
                    candidate_ref=candidate.start_time_utc.isoformat(),
                    decision="matched",
                    reasons=json.dumps(decision.reasons),
                    inputs=json.dumps(decision.inputs),
                    decided_at=datetime.now(UTC),
                )
            )
            return row.id  # type: ignore[no-any-return]

    conn.execute(
        merge_decision.insert().values(
            athlete_id=athlete_id,
            matched_activity_id=None,
            candidate_ref=candidate.start_time_utc.isoformat(),
            decision="new",
            reasons=json.dumps([f"no match among {len(rows)} candidates in +/-1 day window"]),
            inputs=json.dumps({"candidates_checked": len(rows)}),
            decided_at=datetime.now(UTC),
        )
    )
    return None


def ingest_canonical_batch(
    conn: Connection,
    parquet_dir: Path,
    *,
    athlete_id: str,
    source: str,
    raw_object_id: int,
    sha256: str,
    batch: CanonicalBatch,
    external_id_hint: str | None = None,
) -> IngestResult:
    """Upserts a parsed batch into the schema. Idempotent: a raw object that's already linked
    (same athlete_id, source, external_id) is a no-op — see activity_source_link's unique
    constraint, which is the actual idempotency mechanism this relies on.

    `external_id_hint`: when the caller already has a real, stable, vendor-assigned ID (e.g.
    `garmin_export`'s filename-embedded activity ID, or `garmin_connect`'s own `activityId`),
    pass it here to use directly instead of the device-serial+start-time/sha256 fallback below
    — that fallback exists for `fit_folder`, which has no such ID available.
    """
    if batch.kind != "activity" or batch.activity is None:
        return IngestResult(activity_id=None, created=False)

    a = batch.activity
    external_id = external_id_hint or _derive_external_id(a.start_time_utc, a.device, sha256)

    existing_activity_id = conn.execute(
        select(activity_source_link.c.activity_id).where(
            activity_source_link.c.athlete_id == athlete_id,
            activity_source_link.c.source == source,
            activity_source_link.c.external_id == external_id,
        )
    ).scalar_one_or_none()
    if existing_activity_id is not None:
        assert isinstance(existing_activity_id, str)
        return IngestResult(activity_id=existing_activity_id, created=False)

    # Comprehensive cataloging regardless of match outcome — never drop an unknown field.
    for m in a.extra_metrics:
        get_or_register_metric(conn, metric_key=m.key, source=source, category="activity")
    for key in a.unrecognized_field_keys:
        get_or_register_metric(conn, metric_key=key, source=source, category="unknown")

    device_id = _upsert_device(conn, athlete_id, a.device) if a.device else None
    matched_id = _find_merge_match(
        conn, athlete_id, ActivityCandidate(a.start_time_utc, a.duration_s, a.sport)
    )

    now = datetime.now(UTC)
    created = matched_id is None
    if matched_id is not None:
        activity_id = matched_id
    else:
        activity_id = str(ULID())
        conn.execute(
            activity.insert().values(
                id=activity_id,
                athlete_id=athlete_id,
                start_time_utc=a.start_time_utc,
                utc_offset_s=a.utc_offset_s,
                tz_name=None,
                sport=a.sport,
                sub_sport=a.sub_sport,
                name=a.name,
                description=None,
                duration_s=a.duration_s,
                moving_duration_s=a.moving_duration_s,
                distance_m=a.distance_m,
                elevation_gain_m=a.elevation_gain_m,
                calories=a.calories,
                device_id=device_id,
                primary_source=source,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )

        seen_metric_keys: set[str] = set()
        for m in a.extra_metrics:
            if m.key in seen_metric_keys:
                continue
            seen_metric_keys.add(m.key)
            conn.execute(
                activity_metric.insert().values(
                    athlete_id=athlete_id,
                    activity_id=activity_id,
                    metric_key=m.key,
                    value_num=m.value_num,
                    value_text=m.value_text,
                    unit=m.unit,
                    source=source,
                    created_at=now,
                )
            )

        for lap_row in a.laps:
            conn.execute(
                lap.insert().values(
                    athlete_id=athlete_id,
                    activity_id=activity_id,
                    lap_index=lap_row.lap_index,
                    start_time_utc=lap_row.start_time_utc,
                    duration_s=lap_row.duration_s,
                    distance_m=lap_row.distance_m,
                    avg_hr=lap_row.avg_hr,
                    max_hr=lap_row.max_hr,
                    avg_speed_mps=lap_row.avg_speed_mps,
                )
            )

        for split_row in a.splits:
            conn.execute(
                split_table.insert().values(
                    athlete_id=athlete_id,
                    activity_id=activity_id,
                    split_index=split_row.split_index,
                    split_type=split_row.split_type,
                    start_time_utc=split_row.start_time_utc,
                    end_time_utc=split_row.end_time_utc,
                    duration_s=split_row.duration_s,
                    distance_m=split_row.distance_m,
                )
            )

        if a.route_start:
            encoded = polyline_codec.encode(a.route_points) if a.route_points else None
            bbox = a.route_bbox
            conn.execute(
                route_geom.insert().values(
                    activity_id=activity_id,
                    athlete_id=athlete_id,
                    encoded_polyline=encoded,
                    simplified_polyline=encoded,
                    min_lat=bbox[0] if bbox else None,
                    min_lng=bbox[1] if bbox else None,
                    max_lat=bbox[2] if bbox else None,
                    max_lng=bbox[3] if bbox else None,
                    start_lat=a.route_start[0],
                    start_lng=a.route_start[1],
                    end_lat=a.route_end[0] if a.route_end else None,
                    end_lng=a.route_end[1] if a.route_end else None,
                )
            )

        if a.stream:
            rel_path, n_samples, channels = write_activity_stream(
                parquet_dir, athlete_id, activity_id, a.stream
            )
            conn.execute(
                activity_stream.insert().values(
                    activity_id=activity_id,
                    athlete_id=athlete_id,
                    parquet_path=rel_path,
                    n_samples=n_samples,
                    channels=json.dumps(channels),
                    sample_rate_hint=None,
                )
            )

    conn.execute(
        activity_source_link.insert().values(
            athlete_id=athlete_id,
            activity_id=activity_id,
            source=source,
            external_id=external_id,
            raw_object_id=raw_object_id,
            ingested_at=now,
        )
    )

    return IngestResult(activity_id=activity_id, created=created)


def import_from_folder(
    conn: Connection,
    archive_root: Path,
    parquet_dir: Path,
    *,
    athlete_id: str,
    folder: Path,
    since: datetime | None = None,
) -> IngestRunSummary:
    # Local import: ingest_dispatch imports IngestResult/ingest_canonical_batch from this
    # module, so importing it at module level here would be circular.
    from sporthealth.ingest_dispatch import ingest_fit_bytes

    adapter = FitFolderAdapter(folder)
    health = adapter.health_check()
    if not health.ok:
        raise RuntimeError(f"fit_folder health check failed: {health.detail}")

    started_at = datetime.now(UTC)
    result = conn.execute(
        ingest_run.insert().values(
            athlete_id=athlete_id,
            source=adapter.name,
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
    for ref in adapter.list_changed(since or _EPOCH):
        summary.items_seen += 1
        path = Path(ref.locator)
        try:
            if path.suffix.lower() == ".fit":
                content = path.read_bytes()
                dispatch_result = ingest_fit_bytes(
                    conn,
                    archive_root,
                    parquet_dir,
                    athlete_id=athlete_id,
                    source=adapter.name,
                    content=content,
                    locator=ref.locator,
                )
                conn.commit()
                if dispatch_result.created:
                    summary.items_new += 1
                continue

            content = path.read_bytes()
            if _DAILY_SUMMARY_JSON_RE.match(path.name):
                json_kind = "daily_summary_json"
                health_batch = parse_daily_summary_json(content)
            else:
                json_kind = "hydration_json"
                health_batch = parse_hydration_json(content)

            archive_raw_bytes(
                conn,
                archive_root,
                athlete_id=athlete_id,
                source=adapter.name,
                kind=json_kind,
                content=content,
                locator=ref.locator,
            )
            health_result = ingest_health_batch(
                conn, parquet_dir, athlete_id=athlete_id, source=adapter.name, batch=health_batch
            )
            conn.commit()
            if health_result.observations_new > 0 or health_result.sleep_sessions_new > 0:
                summary.items_new += 1
        except Exception as e:  # one bad file must not abort the whole run
            conn.rollback()
            summary.errors.append({"file": ref.locator, "error": str(e)})

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
