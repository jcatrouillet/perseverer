"""Per-run VDOT (see vdot.py's own docstring for the formula and rationale) -- computed and
stored as an ordinary `activity_metric` row, the same EAV mechanism every other derived/overlaid
per-activity value already uses (e.g. `strava.session.avg_heart_rate`), rather than a new schema
column. `source="perseverer"` distinguishes it from anything a vendor actually reported, matching
the `strava.*`/`fit.*` provenance-by-namespace convention.

Full recompute on every call, same precedent as `fitness.py::refresh_fitness_rollup` and
`insights/engine.py::refresh_insights` (both called alongside this at every ingest entry point):
delete every VDOT row for the athlete, then reinsert one per current `sport == "running"`
activity. This is what keeps VDOT correct after a sport correction (`sport_override.py`) moves an
activity into or out of "running", or after any other retroactive correction to distance/duration
-- exactly the same "a retroactive correction invalidates everything forward from it" reasoning
`fitness.py` already documents, applied here instead of trying to track which activities changed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
from sqlalchemy import Connection, select

from perseverer.db.schema import activity, activity_metric, activity_stream
from perseverer.metrics.registry import get_or_register_metric
from perseverer.vdot import compute_gap_factor, compute_vdot

VDOT_METRIC_KEY = "perseverer.performance.vdot"
_SOURCE = "perseverer"


def _gap_factor_from_stream(parquet_dir: Path, stream_row: object) -> float | None:
    channels = json.loads(stream_row.channels)  # type: ignore[attr-defined]
    if "distance_m" not in channels or "altitude_m" not in channels:
        return None
    full_path = parquet_dir / stream_row.parquet_path  # type: ignore[attr-defined]
    if not full_path.exists():
        return None
    table = pq.read_table(full_path, columns=["distance_m", "altitude_m"])
    distances = table.column("distance_m").to_pylist()
    altitudes = table.column("altitude_m").to_pylist()
    return compute_gap_factor(distances, altitudes)


def refresh_vdot(conn: Connection, parquet_dir: Path, *, athlete_id: str) -> int:
    """Returns the number of activities a VDOT value was written for."""
    get_or_register_metric(
        conn,
        metric_key=VDOT_METRIC_KEY,
        source=_SOURCE,
        display_name="VDOT",
        unit_si=None,
        category="performance",
        value_type="numeric",
    )

    conn.execute(
        activity_metric.delete().where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key == VDOT_METRIC_KEY,
        )
    )

    streams_by_activity_id = {
        row.activity_id: row
        for row in conn.execute(
            select(
                activity_stream.c.activity_id,
                activity_stream.c.parquet_path,
                activity_stream.c.channels,
            ).where(activity_stream.c.athlete_id == athlete_id)
        )
    }

    rows = conn.execute(
        select(activity.c.id, activity.c.distance_m, activity.c.moving_duration_s).where(
            activity.c.athlete_id == athlete_id,
            activity.c.sport == "running",
            activity.c.deleted_at.is_(None),
        )
    ).fetchall()

    now = datetime.now(UTC).replace(tzinfo=None)
    written = 0
    for row in rows:
        stream_row = streams_by_activity_id.get(row.id)
        gap_factor = _gap_factor_from_stream(parquet_dir, stream_row) if stream_row else None
        vdot = compute_vdot(row.distance_m, row.moving_duration_s, gap_factor=gap_factor)
        if vdot is None:
            continue
        conn.execute(
            activity_metric.insert().values(
                athlete_id=athlete_id,
                activity_id=row.id,
                metric_key=VDOT_METRIC_KEY,
                value_num=vdot,
                value_text=None,
                unit=None,
                source=_SOURCE,
                created_at=now,
            )
        )
        written += 1

    return written
