"""Per-activity stream storage: full-resolution time-series as Parquet, never as SQLite rows.

See CLAUDE.md: "SQLite holds what you filter and join on, Parquet holds what you plot."
Downsampling into low/medium/high tiers is deferred to Phase 3 (serving concern, not an
ingestion one) — this writes the single full-resolution file those tiers would derive from.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from perseverer.fit.types import StreamPoint
from perseverer.health.types import HealthStreamPoint


def write_activity_stream(
    parquet_dir: Path, athlete_id: str, activity_id: str, points: list[StreamPoint]
) -> tuple[str, int, list[str]]:
    """Writes one Parquet file for an activity's stream.

    Returns (relative_path, n_samples, channel_names). Overwrites any existing file for this
    activity_id — parsing is a pure function, so re-importing converges to the same output.
    Caller is responsible for skipping this entirely when `points` is empty (e.g. strength
    training has no per-second record stream).
    """
    channel_names = sorted({key for point in points for key in point.values})

    timestamps = [point.timestamp_utc for point in points]
    columns: dict[str, list[float | None]] = {
        channel: [point.values.get(channel) for point in points] for channel in channel_names
    }

    table = pa.table(
        {
            "timestamp_utc": pa.array(timestamps, type=pa.timestamp("us", tz="UTC")),
            **columns,
        }
    )

    relative_path = f"{athlete_id}/{activity_id}.parquet"
    full_path = parquet_dir / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, full_path)

    return relative_path, len(points), channel_names


def write_health_stream(
    parquet_dir: Path,
    athlete_id: str,
    metric_key: str,
    year_month: str,
    points: list[HealthStreamPoint],
) -> tuple[str, int]:
    """Writes/merges intraday health stream points into the (metric_key, year_month) Parquet
    file. Unlike `write_activity_stream` (one file, always fully rewritten from one activity's
    parse), a health-stream file is built up incrementally: separate daily source files
    (`WELLNESS`/`HRV_STATUS` FIT files, one per day) all contribute to the same month's file
    across many ingest calls. Existing points are read back, new points are merged in keyed by
    timestamp (last write wins — re-ingesting the same day is idempotent), and the file is
    rewritten sorted. Returns (relative_path, total_n_samples_after_merge).
    """
    relative_path = f"{athlete_id}/health/{metric_key}/{year_month}.parquet"
    full_path = parquet_dir / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)

    # Every timestamp is normalized to naive here, regardless of which form it arrives in --
    # pyarrow round-trips a tz("UTC") column back as tz-aware Python datetimes on read, while
    # `HealthStreamPoint.timestamp_utc` producers disagree with each other (fit_parser.py's are
    # tz-aware; health/json_parser.py's are naive, per this project's own naive-implicitly-UTC
    # DateTime convention) -- without normalizing both sides the same way, a same-instant reading
    # could land as two different dict keys, and `sorted()` raises outright if the mix includes
    # both an aware and a naive key (a real bug: this merge path had never been exercised by more
    # than one write to the same file before the day-2-onward case this fixes was found).
    merged: dict[object, float] = {}
    if full_path.exists():
        existing = pq.read_table(full_path)
        existing_ts = existing.column("timestamp_utc").to_pylist()
        existing_val = existing.column("value").to_pylist()
        for ts, value in zip(existing_ts, existing_val, strict=True):
            merged[ts.replace(tzinfo=None)] = value

    for point in points:
        merged[point.timestamp_utc.replace(tzinfo=None)] = point.value

    ordered = sorted(merged.items())
    table = pa.table(
        {
            "timestamp_utc": pa.array([ts for ts, _ in ordered], type=pa.timestamp("us", tz="UTC")),
            "value": [v for _, v in ordered],
        }
    )
    pq.write_table(table, full_path)
    return relative_path, len(ordered)
