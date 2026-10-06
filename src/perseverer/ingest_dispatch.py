"""Unified per-FIT-file dispatch: archives once, tries activity parsing, falls back to health
parsing, else no-ops (truly unrecognized — e.g. a corrupt file). Every entry point that can
receive an arbitrary `.fit` file (`fit_folder`, `garmin_export`, and `rebuild`'s replay) goes
through this, so activity and health FIT files are handled identically everywhere. Confirmed
necessary on real data: the same device export directory contains both kinds side by side —
see docs/adr/0004-phase-2-health-ingestion.md.

`garmin_connect` does not use this: it only ever downloads activity FIT files, so its direct
`parse_fit` + `ingest_canonical_batch` call is deliberately unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection

from perseverer.adapters.fit_folder import IngestResult, ingest_canonical_batch
from perseverer.archive import archive_raw_bytes
from perseverer.fit.parser import parse_fit
from perseverer.health.fit_parser import parse_health_fit
from perseverer.health.ingest import HealthIngestResult, ingest_health_batch

# Archived under one generic kind, not "fit_activity"/"fit_wellness"/etc.: raw-first means we
# archive before we know which this is. `rebuild.py` replays any raw_object whose kind starts
# with "fit" (this stays backward-compatible with Phase 1/2's "fit_activity" rows).
FIT_KIND = "fit"


@dataclass(frozen=True)
class DispatchResult:
    raw_object_id: int
    activity_result: IngestResult | None = None
    health_result: HealthIngestResult | None = None

    @property
    def created(self) -> bool:
        if self.activity_result is not None:
            return self.activity_result.created
        if self.health_result is not None:
            return (
                self.health_result.observations_new > 0 or self.health_result.sleep_sessions_new > 0
            )
        return False

    def affected_local_dates(self) -> set[str]:
        """Distinct local_dates newly touched by this dispatch -- what callers accumulate
        across an ingest run and feed to rollups.refresh_daily_rollup once each, not once per
        file. See docs/adr/0006-phase-3-read-api-and-rollups.md decision 3.
        """
        dates: set[str] = set()
        if self.activity_result is not None and self.activity_result.local_date is not None:
            dates.add(self.activity_result.local_date)
        if self.health_result is not None:
            dates |= self.health_result.affected_local_dates
        return dates


def ingest_fit_bytes(
    conn: Connection,
    archive_root: Path,
    parquet_dir: Path,
    *,
    athlete_id: str,
    source: str,
    content: bytes,
    locator: str | None = None,
    external_id_hint: str | None = None,
) -> DispatchResult:
    """Archives one FIT file, then ingests it as an activity or, failing that, as health data -- the
    single entry point every FIT-reading adapter shares.
    """
    raw_id = archive_raw_bytes(
        conn,
        archive_root,
        athlete_id=athlete_id,
        source=source,
        kind=FIT_KIND,
        content=content,
        locator=locator,
        external_id=external_id_hint,
    )
    sha256 = hashlib.sha256(content).hexdigest()

    batch = parse_fit(content)
    if batch.kind == "activity":
        activity_result = ingest_canonical_batch(
            conn,
            parquet_dir,
            athlete_id=athlete_id,
            source=source,
            raw_object_id=raw_id,
            sha256=sha256,
            batch=batch,
            external_id_hint=external_id_hint,
        )
        return DispatchResult(raw_object_id=raw_id, activity_result=activity_result)

    health_batch = parse_health_fit(content)
    if not health_batch.is_empty():
        health_result = ingest_health_batch(
            conn, parquet_dir, athlete_id=athlete_id, source=source, batch=health_batch
        )
        return DispatchResult(raw_object_id=raw_id, health_result=health_result)

    return DispatchResult(raw_object_id=raw_id)
