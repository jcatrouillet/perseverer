"""Per-activity stream storage: full-resolution time-series as Parquet, never as SQLite rows.

See CLAUDE.md: "SQLite holds what you filter and join on, Parquet holds what you plot."
Downsampling into low/medium/high tiers is deferred to Phase 3 (serving concern, not an
ingestion one) — this writes the single full-resolution file those tiers would derive from.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from sporthealth.fit.types import StreamPoint


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
