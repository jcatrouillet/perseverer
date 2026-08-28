"""Whole-activity average Grade Adjusted Pace, computed once per running activity from its own
raw stream and stored as an ordinary `activity_metric` row -- the same EAV mechanism
`performance.py` (VDOT) and `pace_bands.py` already use, one metric_key, not a new table.

The model itself (Minetti et al. 2002 energy-cost-of-running polynomial for uphill/flat, plus a
softened downhill curve matching Strava's own published post-2017 points -- see
`_grade_adjusted_time_factor`'s own docstring) is duplicated from `frontend/src/gap.ts`, not
shared -- same cross-language duplication precedent as `weatherCode.ts`/`weather_code.py` and
`personalRecords`/`rules_pb.py`. The frontend's own copy computes a *per-point* series for the
GAP chart panel (ActivityCharts.tsx), smoothed over a real horizontal window since a single
point-to-point grade off raw GPS/barometric samples is mostly noise -- this module doesn't need
that: averaged over a whole activity's thousands of samples, point-to-point noise on individual
intervals washes out in the aggregate, and a summary "how hard was this run, effort-adjusted"
number doesn't need the same visual smoothing a chart line does.

Storage is SI throughout (project convention, CLAUDE.md principle 6): average grade-adjusted
*speed* in m/s, not a pre-formatted "min/km" pace string -- the presentation layer (API schema /
frontend) converts, the same way an activity's own instantaneous speed stream is stored in m/s
and only ever formatted to pace at read time.

Full recompute on every call, same precedent as `performance.py::refresh_vdot` and
`pace_bands.py::refresh_pace_bands` (called alongside both at every ingest entry point): delete
every row for the athlete under this metric_key, then reinsert from every current
`sport == "running"` activity that has a stream with both `altitude_m` and `distance_m` channels.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_left
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
from sqlalchemy import Connection, select

from perseverer.db.schema import activity, activity_metric, activity_stream
from perseverer.metrics.registry import get_or_register_metric

_SOURCE = "perseverer"
AVG_GAP_METRIC_KEY = "perseverer.performance.avg_gap_speed_mps"

# Same threshold pace_bands.py's own compute_pace_band_seconds uses for "stopped, not just slow"
# -- a paused/stationary interval contributes no real grade-adjusted effort either way.
_STATIONARY_MPS_FLOOR = 0.3

# Matches frontend/src/gap.ts exactly -- Minetti's own data only validates out to roughly +-45%
# grade; clamping keeps one noisy elevation spike from extrapolating into a nonsensical multiplier.
_FLAT_COST = 3.6  # C(0)
_MAX_GRADE = 0.45

# The three points Strava's own "An Improved GAP Model" (2017) post disclosed for their
# post-2017 downhill curve, as a speed multiplier f(grade) -- see frontend/src/gap.ts's own
# module docstring for the full reasoning (confirmed by reading Strava's posts directly, not
# assumed): Minetti overcorrects downhill effort badly relative to Strava's own published chart,
# and Strava has never released the replacement model's exact coefficients. The dip sits exactly
# at half of _DOWNHILL_RECOVERY_GRADE (-9% = -18%/2) by construction of the sine bump below, so
# there's no separate "dip grade" constant to carry -- it falls out of the formula itself.
_DOWNHILL_DIP_FACTOR = 0.88
_DOWNHILL_RECOVERY_GRADE = -0.18


def _cost_of_running(grade_fraction: float) -> float:
    """C(i) = 155.4*i^5 - 30.4*i^4 - 43.3*i^3 + 46.3*i^2 + 19.5*i + 3.6 -- energy cost of running,
    J/(kg*m), at grade `i` (fraction, e.g. 0.1 = 10% uphill). Ported from frontend/src/gap.ts's
    own `costOfRunning`, which cites Minetti et al. (2002). Uphill/flat only -- see
    `_grade_adjusted_time_factor` for downhill."""
    i = max(-_MAX_GRADE, min(_MAX_GRADE, grade_fraction))
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + _FLAT_COST


def _downhill_speed_factor(grade_fraction: float) -> float:
    """Ported from frontend/src/gap.ts's own `downhillSpeedFactor` -- a smooth bump through
    (0, 1.0) -> (_DOWNHILL_DIP_GRADE, _DOWNHILL_DIP_FACTOR) -> (_DOWNHILL_RECOVERY_GRADE, 1.0),
    flat at 1.0 beyond the recovery point. Only ever called with grade_fraction <= 0."""
    if grade_fraction <= _DOWNHILL_RECOVERY_GRADE:
        return 1.0
    return 1 - (1 - _DOWNHILL_DIP_FACTOR) * math.sin(
        (math.pi * grade_fraction) / _DOWNHILL_RECOVERY_GRADE
    )


def _grade_adjusted_time_factor(grade_fraction: float) -> float:
    """Multiplier on actual elapsed time to get grade-adjusted (flat-equivalent) time for a
    segment run at `grade_fraction` -- ported from frontend/src/gap.ts's own
    `gradeAdjustedPaceMinPerKm` ratio (same uphill-Minetti/downhill-Strava-points split), just
    expressed as a time multiplier since callers here already have elapsed seconds rather than a
    pace value."""
    if grade_fraction < 0:
        return 1 / _downhill_speed_factor(grade_fraction)
    return _FLAT_COST / _cost_of_running(grade_fraction)


def compute_avg_gap_speed_mps(
    timestamps_utc: Sequence[datetime],
    distances_m: Sequence[float | None],
    altitudes_m: Sequence[float | None],
) -> float | None:
    """Distance-weighted average grade-adjusted speed across the whole stream: each interval's
    actual time is scaled by `_grade_adjusted_time_factor` (same relationship
    frontend/src/gap.ts's `gradeAdjustedPaceMinPerKm` uses) and weighted by that interval's own
    horizontal distance, so `total grade-adjusted time / total distance` is the equivalent flat-
    ground pace that would cover the same distance at the same total effort. All three sequences
    must be the same length and share index order, as read straight off one Parquet table's
    columns. None when there's nothing usable to average (too few samples, or every interval
    missing a channel/stationary)."""
    total_gap_time_s = 0.0
    total_distance_m = 0.0
    for i in range(len(timestamps_utc) - 1):
        d0, d1 = distances_m[i], distances_m[i + 1]
        a0, a1 = altitudes_m[i], altitudes_m[i + 1]
        if d0 is None or d1 is None or a0 is None or a1 is None:
            continue
        dist_delta = d1 - d0
        if dist_delta <= 0:
            continue
        dt_s = (timestamps_utc[i + 1] - timestamps_utc[i]).total_seconds()
        if dt_s <= 0:
            continue
        speed_mps = dist_delta / dt_s
        if speed_mps < _STATIONARY_MPS_FLOOR:
            continue
        grade = (a1 - a0) / dist_delta
        # actual_pace_s_per_m * grade_adjusted_time_factor(grade) -- the grade-adjusted-time
        # contribution of this interval simplifies to dt_s * factor directly (actual_pace_s_per_m
        # * dist_delta == dt_s by construction), no separate pace variable needed.
        total_gap_time_s += dt_s * _grade_adjusted_time_factor(grade)
        total_distance_m += dist_delta
    if total_distance_m <= 0 or total_gap_time_s <= 0:
        return None
    return total_distance_m / total_gap_time_s


def compute_lap_gap_speeds_mps(
    lap_start_epoch_s: Sequence[float],
    stream_epoch_s: Sequence[float],
    distances_m: Sequence[float | None],
    altitudes_m: Sequence[float | None],
) -> list[float | None]:
    """One average grade-adjusted speed per lap -- computed by slicing the activity's own full-
    resolution stream at each lap's own wall-clock boundary and reusing `compute_avg_gap_speed_mps`
    on that slice, rather than a second averaging implementation. Same one-lap-is-one-time-range
    convention `frontend/src/gap.ts::computeLapGapsMinPerKm` uses -- a lap's own end is the next
    lap's start, or the stream's last point for the final lap -- except this is the real distance-
    weighted per-interval average (every stream sample within the lap contributes, matching
    `refresh_avg_gap`'s own whole-activity computation) rather than that frontend function's
    single net-elevation-change-over-the-whole-lap approximation, which is what actually let this
    move server-side: a headless caller can now read the same number the API already serves for a
    whole activity, per lap, without rendering a page.

    `lap_start_epoch_s`/`stream_epoch_s` are Unix seconds (epoch), not `datetime` -- the caller
    reads the Parquet stream via DuckDB's own `epoch(timestamp_utc)`, the same convention
    `transport_mix.py`/`stream_query.py` already use for a request-time Parquet read, so there's
    no tz-awareness mismatch to reconcile between a naive-implicit-UTC `lap.start_time_utc` and
    whatever tzinfo a Parquet timestamp round-trips as. `stream_epoch_s` must be sorted ascending
    (as read ordered by timestamp, matching every other reader of this file) so `bisect_left` can
    locate each lap's own boundary in it.

    All three stream sequences must be the same length and share index order. `None` per lap
    wherever that lap's own slice has too few points, is missing a channel, or the lap falls
    entirely after the stream's last sample -- same contract as `compute_avg_gap_speed_mps` itself.
    """
    if (
        not stream_epoch_s
        or len(stream_epoch_s) != len(distances_m)
        or len(stream_epoch_s) != len(altitudes_m)
    ):
        return [None] * len(lap_start_epoch_s)

    results: list[float | None] = []
    for i, start_s in enumerate(lap_start_epoch_s):
        start_idx = bisect_left(stream_epoch_s, start_s)
        end_s = lap_start_epoch_s[i + 1] if i + 1 < len(lap_start_epoch_s) else None
        end_idx = (
            bisect_left(stream_epoch_s, end_s) if end_s is not None else len(stream_epoch_s) - 1
        )
        if start_idx >= len(stream_epoch_s) or end_idx <= start_idx:
            results.append(None)
            continue
        slice_ts = [
            datetime.fromtimestamp(s, tz=UTC) for s in stream_epoch_s[start_idx : end_idx + 1]
        ]
        results.append(
            compute_avg_gap_speed_mps(
                slice_ts,
                distances_m[start_idx : end_idx + 1],
                altitudes_m[start_idx : end_idx + 1],
            )
        )
    return results


def refresh_avg_gap(conn: Connection, parquet_dir: Path, *, athlete_id: str) -> int:
    """Returns the number of activities an average-GAP value was written for."""
    get_or_register_metric(
        conn,
        metric_key=AVG_GAP_METRIC_KEY,
        source=_SOURCE,
        display_name="Average Grade Adjusted Pace",
        unit_si="m/s",
        category="performance",
        value_type="numeric",
    )

    conn.execute(
        activity_metric.delete().where(
            activity_metric.c.athlete_id == athlete_id,
            activity_metric.c.metric_key == AVG_GAP_METRIC_KEY,
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

    activity_ids = (
        conn.execute(
            select(activity.c.id).where(
                activity.c.athlete_id == athlete_id,
                activity.c.sport == "running",
                activity.c.deleted_at.is_(None),
            )
        )
        .scalars()
        .all()
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    written = 0
    for activity_id in activity_ids:
        stream_row = streams_by_activity_id.get(activity_id)
        if stream_row is None:
            continue
        channels = json.loads(stream_row.channels)
        if "altitude_m" not in channels or "distance_m" not in channels:
            continue
        full_path = parquet_dir / stream_row.parquet_path
        if not full_path.exists():
            continue

        table = pq.read_table(full_path, columns=["timestamp_utc", "distance_m", "altitude_m"])
        timestamps = table.column("timestamp_utc").to_pylist()
        distances = table.column("distance_m").to_pylist()
        altitudes = table.column("altitude_m").to_pylist()
        if len(timestamps) < 2:
            continue

        avg_gap_speed = compute_avg_gap_speed_mps(timestamps, distances, altitudes)
        if avg_gap_speed is None:
            continue

        conn.execute(
            activity_metric.insert().values(
                athlete_id=athlete_id,
                activity_id=activity_id,
                metric_key=AVG_GAP_METRIC_KEY,
                value_num=avg_gap_speed,
                value_text=None,
                unit="m/s",
                source=_SOURCE,
                created_at=now,
            )
        )
        written += 1

    return written
