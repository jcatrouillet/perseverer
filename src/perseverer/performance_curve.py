"""Performance curve: for a chosen metric (`pace`, `gap`, or `heart_rate`) and a chosen date
range, the best *sustained* average value for each of a fixed set of durations, across *every*
qualifying activity in that range -- the single best D-second-long window anywhere, regardless of
which activity or when it occurred, not one activity's own average. The same idea as cycling's
"critical power curve" (GoldenCheetah, TrainingPeaks) or Runalyze's own "Heart Rate Curve" --
plot duration (log scale) against best-value and the curve's own shape says something a single
whole-activity average never can: how intensity actually degrades with duration for this athlete.

**This is genuinely new territory for this codebase.** No sliding-window "best effort of
duration D" logic exists anywhere else -- `frontend/src/runningStats.ts::personalRecords` (a
materially different, coarser approximation) explicitly documents the gap it works around: "An
honest approximation, not Strava/intervals.icu's real 'best effort' feature: that extracts the
fastest continuous segment of exactly the target distance from every activity's full per-second
stream." `best_window_over_stream` below is that missing primitive, written once, here.

**Gap disqualification, not silent bridging** (the same never-fabricate discipline
`race_readiness.py`/`weather.py` already apply elsewhere): a candidate window only counts as a
real "sustained" effort if (a) a real window of close to the target duration actually exists at
that start point (an activity shorter than a bucket's own duration is simply skipped for it,
never extrapolated), and (b) no single inter-sample gap inside the window exceeds `_MAX_GAP_S` --
a real pause or GPS/HR-strap dropout, not just ordinary sample jitter. A window with several
small gaps under that threshold is still accepted (this app's own explicit, adjustable policy
choice, not a claim of perfect physiological continuity) -- the far more common and more
seriously misleading failure mode this guards against is a genuine stop or signal loss silently
counted as sustained effort, not sub-threshold GPS noise.

**Scope**: `pace`/`gap` are running-only, always, regardless of any `sports` filter passed in --
matching every other pace-specific feature in this app's own exact-`sport=="running"` convention
(GAP's own Minetti cost-of-running model has no meaning for any other gait). `heart_rate` instead
takes an athlete-chosen `sports` filter (checkboxes in the frontend, default every sport) since a
hard bike ride or hiit session is a real sustained HR effort too, one this app has no reason to
exclude the way it excludes non-running pace.

**GAP stream**: reuses `gap.py::compute_gap_adjusted_distances` (the same per-interval
grade-adjusted-equivalent-distance array `compute_avg_gap_speed_mps` sums internally) rather than
re-deriving the Minetti/windowed-grade model a second time -- cumulative-summed, it feeds the
exact same sliding-window search as plain pace's own raw cumulative distance, just over a
different distance series.

**Reference values, shown alongside, never reconciled**: the athlete's own already-computed
threshold pace/HR (`performance_daily_rollup`, the same VDOT-based model `threshold_analysis.py`
already surfaces) are returned alongside the curve so the frontend can draw them as reference
lines the curve's own real shape can be compared against visually -- never blended into a new
number, the same posture Race Readiness's own VDOT-based "prognosis" already establishes toward
its own volume-adequacy readiness percentage. `None` (never fabricated) wherever that rollup
hasn't computed a value yet.

**One combined DuckDB read across every qualifying activity's Parquet file**, not one query per
activity -- a new pattern for this codebase (every existing Parquet read, `stream_query.py`
included, is one `read_parquet(one_path)` call), confirmed live against the installed DuckDB
version (1.5.5) to accept a bound Python list of paths for `read_parquet(?, filename=true)`
before relying on it, per this project's own "verify against real data, don't guess" discipline.

**Deliberately request-time, not rollup-backed** -- the same "bounded, occasional diagnostic
lookup" exception `vo2max_analysis.py`/`threshold_analysis.py`/`race_readiness.py` already
establish. Verified empirically (not assumed), not just assumed fast because the algorithm is
O(N), against this project's own real multi-year history (~1,000 running activities): under 2s
for the "last 3 months"/"last 6 months" presets, 3-4s for "last year", and 8-15s for "all time"
(pace fastest, GAP slowest -- it also runs `gap.py::compute_gap_adjusted_distances`'s own
grade-smoothing pass over every activity). Both `best_window_over_stream` and
`compute_gap_adjusted_distances`'s own windowed-grade calculation were rewritten from a
per-sample Python loop to vectorized numpy after this first measurement found the naive
implementation too slow for "all time" (24-40s) -- see each function's own docstring for what
changed and by how much.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import numpy as np
from sqlalchemy import Connection, select

from perseverer.db.schema import (
    activity,
    activity_stream,
    activity_trim_override,
    performance_daily_rollup,
)
from perseverer.gap import compute_gap_adjusted_distances

Metric = Literal["pace", "gap", "heart_rate"]

# 1 second through 2 hours -- a superset of the reference product's own shown labels. A bucket
# longer than a given activity's own duration is simply skipped for it, never extrapolated; a
# bucket with no qualifying window across *every* activity in range is omitted from the curve
# entirely (never a fabricated point).
DURATION_BUCKETS_S: tuple[int, ...] = (
    1, 5, 10, 15, 30, 60, 120, 180, 300, 600, 900, 1200, 1800, 2700, 3600, 5400, 7200,
)

# A real pause or signal dropout, not ordinary GPS/HR-strap jitter -- see module docstring for
# why a window with smaller gaps under this threshold is still accepted.
_MAX_GAP_S = 15.0
# How close a candidate window's own actual span must be to the target duration to count --
# generous enough to absorb real device sampling jitter without admitting a badly-mismatched
# window (see best_window_over_stream's own docstring).
_SPAN_TOLERANCE = 0.1


@dataclass(frozen=True)
class CurvePoint:
    duration_s: int
    value: float
    # The activity that actually set this bucket's record -- same "driving activity" provenance
    # instinct vo2max_analysis.py::Vo2maxContributor already establishes.
    activity_id: str
    local_date: str


@dataclass(frozen=True)
class PerformanceCurve:
    metric: Metric
    points: list[CurvePoint] = field(default_factory=list)
    # Only the pair relevant to `metric` is ever non-None -- pace/gap get the two threshold
    # paces, heart_rate gets the two threshold HRs plus max HR. Never fabricated: None whenever
    # performance_daily_rollup hasn't computed that value yet.
    threshold_pace_s_per_km: float | None = None
    aerobic_threshold_pace_s_per_km: float | None = None
    threshold_hr_bpm: float | None = None
    aerobic_threshold_hr_bpm: float | None = None
    max_hr_bpm: float | None = None


def best_window_over_stream(
    timestamps_s: Sequence[float],
    values: Sequence[float],
    duration_s: float,
    *,
    mode: Literal["mean", "rate"],
    max_gap_s: float = _MAX_GAP_S,
    span_tolerance: float = _SPAN_TOLERANCE,
) -> float | None:
    """The one sliding-window primitive behind the whole feature -- find the highest windowed
    aggregate of length `duration_s` anywhere in the stream. Always *maximizes* (a caller wanting
    the fastest pace passes a speed-shaped `values`/`mode="rate"` and inverts the result
    afterward, `1000/speed_mps` -> seconds/km; this function itself never distinguishes "lower is
    better").

    `mode="rate"` (pace/GAP): `values` must already be a *cumulative* distance array (same length
    as `timestamps_s`) -- the window's own aggregate is average speed,
    `(values[j]-values[i]) / (timestamps_s[j]-timestamps_s[i])`.

    `mode="mean"` (heart rate): `values` are the raw per-sample readings -- the window's own
    aggregate is their arithmetic mean (a deliberate simplification over a true time-weighted
    mean; real device sampling is close enough to 1Hz that the two are practically
    indistinguishable, and an arithmetic mean of readings is what every reference product treats
    "average HR" as, not a controversial choice).

    Two-pointer scan, O(N) amortized per call (both `i` and `j` only ever move forward): for each
    start index `i`, extend `j` until `timestamps_s[j] - timestamps_s[i]` first reaches
    `duration_s`. The two modes then interpret that boundary differently, matching each metric's
    own real-world convention: **`mode="mean"`** treats the window as `duration_s` worth of
    *samples*, not elapsed time -- the standard convention every reference critical-power/HR-
    curve tool uses (a "5-second" window is 5 consecutive ~1Hz readings, not the 6 readings a
    literal 5-second elapsed-time span would touch) -- so `j` itself is *excluded*, averaging
    samples `[i, j)`. **`mode="rate"`** is a real physical quantity (distance / actual elapsed
    time), so it has no such sample-counting convention to honor: `j` is *included*, and the
    aggregate divides by whatever real elapsed time `[i, j]` actually spans (>= `duration_s`,
    within tolerance) -- exactly the original per-window definition.

    A candidate is rejected (never fabricated as "close enough") when either (a) reaching
    `duration_s` needed a span more than `duration_s * (1 + span_tolerance)` -- the stream is too
    sparse near this start point to give a real answer for this duration -- or (b) `max_gap_s` is
    exceeded by any single inter-sample gap strictly inside the window actually being averaged
    (checked via binary search against a once-per-call list of the stream's own real gap
    positions, not recomputed per window). Once a given start position can't reach `duration_s`
    even using the rest of the array, no later start position can either (a monotonically
    shrinking remainder) -- the scan stops there.

    Returns `None` -- never a fabricated value -- when no valid window exists anywhere in this
    stream for this duration (including when the stream's own total span is already shorter than
    `duration_s`).

    **Implementation note**: vectorized with numpy rather than a per-sample Python loop --
    mathematically the same two-pointer scan (for each start `i`, the minimal `j` with
    `timestamps_s[j] - timestamps_s[i] >= duration_s` is unique and monotonic in `i` regardless of
    how it's found), just found independently per `i` via `np.searchsorted` instead of a single
    forward-only pointer. Confirmed empirically necessary, not a preemptive optimization: a real
    multi-year history (~1,000 running activities, ~3,000 samples each) took 16.5s of Python-loop
    time alone across the 17 duration buckets before this change -- the same "verify, don't guess"
    discipline that added `ix_activity_metric_athlete_key` after `vo2max_analysis.py`'s own factor
    analysis was measured at 33s cold."""
    n = len(timestamps_s)
    if n < 2 or len(values) != n:
        return None
    ts = np.asarray(timestamps_s, dtype=np.float64)
    vals = np.asarray(values, dtype=np.float64)
    if ts[-1] - ts[0] < duration_s:
        return None

    idx = np.arange(n)
    js = np.searchsorted(ts, ts + duration_s, side="left")
    in_range = js < n
    js_c = np.where(in_range, js, n - 1)

    found_span = ts[js_c] - ts
    span_ok = found_span <= duration_s * (1 + span_tolerance)

    gap_bool = (ts[1:] - ts[:-1]) > max_gap_s
    # gap_cum[m] = number of inter-sample gaps at index k < m -- so "any gap with lo <= k < hi"
    # is exactly gap_cum[hi] - gap_cum[lo] > 0, matching has_gap(lo, hi)'s original semantics.
    gap_cum = np.concatenate(([0], np.cumsum(gap_bool)))

    if mode == "mean":
        # Right-exclusive [i, j) -- see docstring above -- so the gap check and sample count both
        # stop at j - 1, the last sample actually included in the window.
        hi = js_c - 1
        has_gap = (gap_cum[hi] - gap_cum[idx]) > 0
        prefix = np.concatenate(([0.0], np.cumsum(vals)))
        counts = js_c - idx
        value = (prefix[js_c] - prefix[idx]) / np.where(counts > 0, counts, 1)
    else:
        has_gap = (gap_cum[js_c] - gap_cum[idx]) > 0
        value = (vals[js_c] - vals[idx]) / np.where(found_span > 0, found_span, 1.0)

    valid = in_range & span_ok & ~has_gap
    if not np.any(valid):
        return None
    return float(np.max(value[valid]))


def _qualifying_activities(
    conn: Connection,
    *,
    athlete_id: str,
    metric: Metric,
    start_date: date,
    end_date: date,
    sports: list[str] | None,
) -> list[tuple[str, str, str, datetime]]:
    """`(activity_id, local_date, parquet_path, start_time_utc)` for every activity in range with
    the stream channel(s) this `metric` needs. `pace`/`gap` hardcode `sport == "running"`
    regardless of `sports` -- see module docstring."""
    required_channels = (
        frozenset({"heart_rate"})
        if metric == "heart_rate"
        else frozenset({"distance_m", "altitude_m"})
    )
    sport_filter = ["running"] if metric != "heart_rate" else sports

    query = (
        select(
            activity.c.id,
            activity.c.local_date,
            activity.c.start_time_utc,
            activity_stream.c.parquet_path,
            activity_stream.c.channels,
        )
        .select_from(activity.join(activity_stream, activity.c.id == activity_stream.c.activity_id))
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            activity.c.local_date >= start_date.isoformat(),
            activity.c.local_date <= end_date.isoformat(),
        )
    )
    if sport_filter is not None:
        if not sport_filter:
            return []
        query = query.where(activity.c.sport.in_(sport_filter))

    out: list[tuple[str, str, str, datetime]] = []
    for row in conn.execute(query):
        channels = frozenset(json.loads(row.channels))
        if not required_channels.issubset(channels):
            continue
        out.append((row.id, row.local_date, row.parquet_path, row.start_time_utc))
    return out


def _trim_windows(
    conn: Connection, *, athlete_id: str
) -> dict[datetime, tuple[float, float]]:
    """`{activity_start_time_utc: (trim_start_s, trim_end_s)}` for every trim override this
    athlete has -- same identity key `get_activity_stream`'s own trim lookup uses. A trimmed
    activity's Parquet file still holds the full original recording (never destructive, see
    `activity_trim.py`'s own docstring), so a "best effort" search must apply the same trim
    window every other view of that activity already respects, or a trimmed-out stretch of car
    travel could silently set a nonsensical short-duration HR/pace record."""
    rows = conn.execute(
        select(
            activity_trim_override.c.activity_start_time_utc,
            activity_trim_override.c.trim_start_s,
            activity_trim_override.c.trim_end_s,
        ).where(activity_trim_override.c.athlete_id == athlete_id)
    ).fetchall()
    return {
        row.activity_start_time_utc: (row.trim_start_s or 0.0, row.trim_end_s or float("inf"))
        for row in rows
    }


def _reference_values(
    conn: Connection, *, athlete_id: str, metric: Metric, as_of: date
) -> dict[str, float | None]:
    """The athlete's own latest `performance_daily_rollup` row (same "read `as_of`'s own row"
    shape `threshold_analysis.py` already uses) -- only the fields `metric` cares about are
    non-None in the result; the rest stay None (never fabricated)."""
    row = conn.execute(
        select(performance_daily_rollup).where(
            performance_daily_rollup.c.athlete_id == athlete_id,
            performance_daily_rollup.c.local_date == as_of.isoformat(),
        )
    ).fetchone()
    if row is None:
        return {}
    if metric == "heart_rate":
        return {
            "threshold_hr_bpm": row.threshold_hr_bpm,
            "aerobic_threshold_hr_bpm": row.aerobic_threshold_hr_bpm,
            "max_hr_bpm": row.max_hr_bpm,
        }
    return {
        "threshold_pace_s_per_km": row.threshold_pace_s_per_km,
        "aerobic_threshold_pace_s_per_km": row.aerobic_threshold_pace_s_per_km,
    }


def compute_performance_curve(
    conn: Connection,
    duckdb_conn: duckdb.DuckDBPyConnection,
    parquet_dir: Path,
    *,
    athlete_id: str,
    metric: Metric,
    start_date: date,
    end_date: date,
    sports: list[str] | None,
    as_of: date,
) -> PerformanceCurve:
    """Orchestration: resolve qualifying activities (SQLite/SQLAlchemy), pull every one of their
    raw streams in one combined DuckDB query, run `best_window_over_stream` per activity per
    duration bucket, and combine across activities per bucket (the higher HR / faster pace wins,
    each `CurvePoint` recording which activity set it). `sports` is ignored entirely for
    `metric in ("pace", "gap")` -- see module docstring."""
    activities = _qualifying_activities(
        conn, athlete_id=athlete_id, metric=metric, start_date=start_date, end_date=end_date,
        sports=sports,
    )
    reference = _reference_values(conn, athlete_id=athlete_id, metric=metric, as_of=as_of)
    if not activities:
        return PerformanceCurve(metric=metric, points=[], **reference)

    trims = _trim_windows(conn, athlete_id=athlete_id)
    paths = [str(parquet_dir / a[2]) for a in activities]
    local_date_by_path = {str(parquet_dir / a[2]): a[1] for a in activities}
    activity_id_by_path = {str(parquet_dir / a[2]): a[0] for a in activities}
    trim_by_path = {
        str(parquet_dir / a[2]): trims[a[3]] for a in activities if a[3] in trims
    }

    columns = ["heart_rate"] if metric == "heart_rate" else ["distance_m", "altitude_m"]
    col_list = ", ".join(columns)
    rows: list[tuple[Any, ...]] = duckdb_conn.execute(
        f"""
        SELECT filename, epoch(timestamp_utc) AS ts, {col_list}
        FROM read_parquet($1, filename=true)
        ORDER BY filename, ts
        """,
        [paths],
    ).fetchall()

    by_path: dict[str, list[tuple[Any, ...]]] = {}
    for row in rows:
        by_path.setdefault(row[0], []).append(row)

    best_per_bucket: dict[int, CurvePoint] = {}
    for path, path_rows in by_path.items():
        trim = trim_by_path.get(path)
        if trim is not None:
            t0 = path_rows[0][1]
            path_rows = [r for r in path_rows if trim[0] <= (r[1] - t0) <= trim[1]]
        if len(path_rows) < 2:
            continue

        timestamps_s = [r[1] for r in path_rows]
        search_mode: Literal["mean", "rate"]
        if metric == "heart_rate":
            hr = [r[2] for r in path_rows]
            valid_pairs = [
                (t, v) for t, v in zip(timestamps_s, hr, strict=True) if v is not None
            ]
            if len(valid_pairs) < 2:
                continue
            valid_timestamps = [t for t, _ in valid_pairs]
            valid_values: list[float] = [float(v) for _, v in valid_pairs]
            search_mode = "mean"
        else:
            distances = [r[2] for r in path_rows]
            altitudes = [r[3] for r in path_rows]
            # Same null-tolerant filtering as the heart-rate branch above -- a dropped
            # distance/altitude reading simply drops that sample from the search, rather than
            # carrying a stale value forward (which could understate a genuine gap) or crashing.
            # A run of several dropped samples in a row still shows up correctly to
            # best_window_over_stream's own gap check, since it compares *kept* samples' real
            # elapsed time, which widens exactly when samples in between were dropped.
            keep = [
                i
                for i in range(len(path_rows))
                if distances[i] is not None and (metric != "gap" or altitudes[i] is not None)
            ]
            if len(keep) < 2:
                continue
            filtered_timestamps_s = [timestamps_s[i] for i in keep]
            filtered_distances = [distances[i] for i in keep]

            if metric == "gap":
                filtered_altitudes = [altitudes[i] for i in keep]
                timestamps_dt = [
                    datetime.fromtimestamp(t, tz=UTC) for t in filtered_timestamps_s
                ]
                per_interval = compute_gap_adjusted_distances(
                    timestamps_dt, filtered_distances, filtered_altitudes
                )
                cumulative = [0.0]
                for d in per_interval:
                    cumulative.append(cumulative[-1] + d)
            else:  # pace
                base = filtered_distances[0]
                cumulative = [d - base for d in filtered_distances]

            valid_timestamps = filtered_timestamps_s
            valid_values = cumulative
            search_mode = "rate"

        activity_id = activity_id_by_path[path]
        local_date = local_date_by_path[path]
        for duration_s in DURATION_BUCKETS_S:
            raw_value = best_window_over_stream(
                valid_timestamps, valid_values, duration_s, mode=search_mode
            )
            if raw_value is None:
                continue
            if metric == "heart_rate":
                value = raw_value
            else:
                if raw_value <= 0:
                    continue
                value = 1000.0 / raw_value  # m/s -> seconds/km; lower is better from here on

            existing = best_per_bucket.get(duration_s)
            if existing is not None:
                is_better = (
                    value > existing.value if metric == "heart_rate" else value < existing.value
                )
                if not is_better:
                    continue
            best_per_bucket[duration_s] = CurvePoint(
                duration_s=duration_s,
                value=value,
                activity_id=activity_id,
                local_date=local_date,
            )

    points = [best_per_bucket[d] for d in DURATION_BUCKETS_S if d in best_per_bucket]
    return PerformanceCurve(metric=metric, points=points, **reference)
