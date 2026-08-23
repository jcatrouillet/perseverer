"""DuckDB-backed downsampling of a single activity's full-resolution Parquet stream into a
fixed number of points for chart display. See
docs/adr/0006-phase-3-read-api-and-rollups.md decisions 3-4.

All bucket-averaging happens in one DuckDB SQL statement against the Parquet file directly --
no Python-side loop, which is exactly the columnar work the Celeron benefits from offloading.
Not under `api/`: independently testable, and this is business logic, not routing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

# Fixed target point counts per tier, not client-specified, to keep the response-size contract
# predictable. See ADR 0006 decision 5.
_TIER_TARGET_POINTS = {"low": 200, "medium": 1000, "high": 20000}


@dataclass(frozen=True)
class ColumnarStream:
    timestamps: list[datetime]
    series: dict[str, list[float | None]]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def downsample(
    con: duckdb.DuckDBPyConnection,
    parquet_path: Path,
    *,
    tier: str,
    channels: list[str],
    available_channels: frozenset[str],
    duration_s: float,
    n_samples: int,
    window: tuple[float, float] | None = None,
) -> ColumnarStream:
    """Bucket-averages `channels` (defaulting to all `available_channels` when empty) from
    `parquet_path` into ~`_TIER_TARGET_POINTS[tier]` points.

    `channels` must be a subset of `available_channels` -- the activity's own
    `activity_stream.channels`, fetched by the caller via SQLAlchemy (the trusted,
    ingest-time-written list). This check is what stands between a client-supplied query
    param and a SQL-identifier-injection-shaped bug: DuckDB's parameter binding covers values,
    not column identifiers, which get interpolated into the query text below. Raises
    `ValueError` for an unknown tier or an unrecognized channel.

    `window`, when given, is `(trim_start_s, trim_end_s)` elapsed seconds from the *file's own*
    first recorded sample -- the Parquet file is never truncated (see activity_trim.py's own
    "never destructive" docstring), so a trimmed activity's stream is still served by filtering
    the same full file down to the kept window at read time, not a second smaller file.
    """
    if tier not in _TIER_TARGET_POINTS:
        raise ValueError(f"unknown tier {tier!r}, expected one of {sorted(_TIER_TARGET_POINTS)}")
    selected = channels or sorted(available_channels)
    unknown = set(selected) - available_channels
    if unknown:
        raise ValueError(f"unrecognized channel(s): {sorted(unknown)}")

    quoted = [_quote(c) for c in selected]
    col_list = ", ".join(quoted)
    target_points = _TIER_TARGET_POINTS[tier]

    window_clause = ""
    window_params: list[str | float] = []
    if window is not None:
        window_clause = (
            " WHERE epoch(timestamp_utc) - (SELECT min(epoch(timestamp_utc)) "
            "FROM read_parquet(?)) BETWEEN ? AND ?"
        )
        window_params = [str(parquet_path), window[0], window[1]]

    if n_samples <= target_points:
        # Already at or under the target -- no bucketing needed, just read it back sorted.
        query = f"""
            SELECT epoch(timestamp_utc) AS bucket_start, {col_list}
            FROM read_parquet(?)
            {window_clause}
            ORDER BY timestamp_utc
        """
        rows = con.execute(query, [str(parquet_path), *window_params]).fetchall()
    else:
        bucket_width_s = max(1, math.ceil(duration_s / target_points))
        agg_list = ", ".join(f"avg({q}) AS {q}" for q in quoted)
        query = f"""
            WITH src AS (
                SELECT epoch(timestamp_utc) AS ts, {col_list}
                FROM read_parquet(?)
                {window_clause}
            )
            SELECT
                min(ts) AS bucket_start,
                {agg_list}
            FROM src
            GROUP BY CAST((ts - (SELECT min(ts) FROM src)) / ? AS BIGINT)
            ORDER BY bucket_start
        """
        rows = con.execute(
            query, [str(parquet_path), *window_params, bucket_width_s]
        ).fetchall()

    timestamps = [datetime.fromtimestamp(row[0], tz=UTC) for row in rows]
    series: dict[str, list[float | None]] = {
        channel: [row[i + 1] for row in rows] for i, channel in enumerate(selected)
    }
    return ColumnarStream(timestamps=timestamps, series=series)
