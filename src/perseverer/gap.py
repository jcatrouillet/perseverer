"""Whole-activity average Grade Adjusted Pace, computed once per running activity from its own
raw stream and stored as an ordinary `activity_metric` row -- the same EAV mechanism
`performance.py` (VDOT) and `pace_bands.py` already use, one metric_key, not a new table.

The model itself (Minetti et al. 2002 energy-cost-of-running polynomial for uphill/flat, plus a
softened downhill curve matching Strava's own published post-2017 points -- see
`_grade_adjusted_time_factor`'s own docstring) is duplicated from `frontend/src/gap.ts`, not
shared -- same cross-language duplication precedent as `weatherCode.ts`/`weather_code.py` and
`personalRecords`/`rules_pb.py`.

Two independent, confirmed real bugs here, found by the same reporter reproducing this module's
own formula against real data, both now fixed:

**Grade is computed over a real +/-15s time window (`_windowed_grade`,
`_DEFAULT_HALF_WINDOW_S`), never from raw sample-to-sample altitude deltas** -- this module
originally assumed averaging over a whole activity's thousands of samples would wash noise out on
its own, but that's false: a run with ~52m of real elevation gain/loss produced ~123m of raw
summed gain (roughly 58% of the signal was GPS/barometric noise), and because the cost curve is
asymmetric around zero grade, *symmetric* altitude noise around a near-zero true grade does not
cancel once converted through that asymmetric curve and averaged -- it produces a systematic
*fast* bias. Widening the grade calculation to a window -- the same idea `frontend/src/gap.ts`'s
own per-point chart series (`gradeAt`) already used for exactly this reason -- fixes it, but only
once the window is keyed on *time*, not distance: a first attempt reused `gradeAt`'s own
fixed-*distance* half-window and under-smoothed exactly the fastest segments (VO2/threshold
reps), since a fixed distance covers less real time the faster a segment is run.

**The whole-activity/whole-lap average is now distance-weighted (equivalent-flat-distance /
moving time), not time-weighted** -- see `compute_avg_gap_speed_mps`'s own docstring for the full
derivation and why the two formulas, identical per-interval at constant speed, diverge once real
pace varies across segments (exactly the shape of an interval workout: fast reps, slow recovery
jogs). This is the physically correct, energy-conserving definition, and matters most whenever
grade *and* pace both vary substantially within one activity/lap -- its effect was modest on the
first reported (largely flat, ~50m gain/loss) activity, but is expected to matter far more on a
hillier one.

**Still open**: after both fixes, the residual gap to intervals.icu on that first activity was
isolated (by holding every other variable fixed) to the softened downhill curve
(`_downhill_speed_factor`) -- removing it entirely (pure Minetti, symmetric, no downhill
treatment at all) reproduced the reporter's own independently-computed reference numbers almost
exactly. That single activity had almost no real descent, though, so it can't actually
discriminate between "Strava's real GAP uses a softer downhill curve than pure Minetti" (this
module's current assumption, sourced from Strava's own published post) and "intervals.icu (and
maybe Strava) is closer to pure Minetti after all" -- both hypotheses fit the same flat data
equally well. Left unchanged pending a real descent-heavy activity to actually tell them apart.

Storage is SI throughout (project convention, AGENTS.md principle 6): average grade-adjusted
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

import numpy as np
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

# A *time* window, not a distance one -- confirmed against real data (reported bug, reproduced):
# a fixed-distance window (this module's first attempt at this fix used
# frontend/src/gap.ts::DEFAULT_HALF_WINDOW_M, a 25m half-window) under-smooths exactly the
# segments where the bias matters most, because it covers less real time at a faster pace, right
# when VO2/threshold reps are running hardest. A 30s moving average (i.e. a +/-15s half-window)
# reproduced intervals.icu's own GAP closely (within a few seconds/km) across a real interval
# session's every segment, independent of that segment's own pace -- see module docstring.
_DEFAULT_HALF_WINDOW_S = 15.0


def _windowed_grade(
    timestamps_utc: Sequence[datetime],
    distances_m: Sequence[float | None],
    altitudes_m: Sequence[float | None],
    i: int,
    half_window_s: float = _DEFAULT_HALF_WINDOW_S,
) -> float | None:
    """Grade at sample `i`, smoothed over a real *time* window rather than the raw sample-to-
    sample delta -- same widening-search shape as `frontend/src/gap.ts`'s own `gradeAt` (walk
    left/right from `i` until each side spans at least `half_window_s`, or the array runs out),
    just keyed on elapsed time instead of distance -- see `_DEFAULT_HALF_WINDOW_S`'s own comment
    for why. `None` if either window edge is missing a distance/altitude value."""
    center_t = timestamps_utc[i]
    n = len(timestamps_utc)
    left = i
    while left > 0 and (center_t - timestamps_utc[left]).total_seconds() < half_window_s:
        left -= 1
    right = i
    while right < n - 1 and (timestamps_utc[right] - center_t).total_seconds() < half_window_s:
        right += 1
    d0, d1 = distances_m[left], distances_m[right]
    a0, a1 = altitudes_m[left], altitudes_m[right]
    if d0 is None or d1 is None or a0 is None or a1 is None:
        return None
    span = d1 - d0
    return (a1 - a0) / span if span > 0 else None


def _windowed_grades_batch(
    timestamps_utc: Sequence[datetime],
    distances_m: Sequence[float | None],
    altitudes_m: Sequence[float | None],
    half_window_s: float = _DEFAULT_HALF_WINDOW_S,
) -> list[float | None]:
    """The grade at every index at once -- the same widening-window definition `_windowed_grade`
    computes one index at a time, found via `np.searchsorted` instead of a per-index Python
    while-loop. Mathematically identical: for a sorted timestamp array, the widening search from
    index `i` always converges on the same left/right boundary regardless of how it's found, so
    this returns exactly what calling `_windowed_grade` at every index would.

    Confirmed necessary, not a preemptive optimization: `compute_gap_adjusted_distances` calling
    the scalar `_windowed_grade` once per sample is O(samples x window-width) in pure Python --
    the dominant cost of `performance_curve.py`'s GAP curve over a real multi-year history
    (~1,000 running activities): roughly halved the curve's own total time (about 27s -> 15s for
    the full history) once vectorized here."""
    n = len(timestamps_utc)
    ts = np.fromiter(
        ((t - timestamps_utc[0]).total_seconds() for t in timestamps_utc),
        dtype=np.float64,
        count=n,
    )
    dist = np.fromiter((np.nan if v is None else v for v in distances_m), dtype=np.float64, count=n)
    alt = np.fromiter((np.nan if v is None else v for v in altitudes_m), dtype=np.float64, count=n)

    # Same boundary semantics as _windowed_grade's own two while-loops: the largest index <= i
    # whose timestamp is >= half_window_s before ts[i] (or 0, if the array runs out first), and
    # the smallest index >= i whose timestamp is >= half_window_s after ts[i] (or n - 1).
    left = np.maximum(np.searchsorted(ts, ts - half_window_s, side="right") - 1, 0)
    right = np.minimum(np.searchsorted(ts, ts + half_window_s, side="left"), n - 1)

    d0, d1 = dist[left], dist[right]
    a0, a1 = alt[left], alt[right]
    span = d1 - d0
    with np.errstate(invalid="ignore", divide="ignore"):
        grade = np.where(span > 0, (a1 - a0) / np.where(span > 0, span, 1.0), np.nan)
    return [None if np.isnan(g) else float(g) for g in grade]


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


def compute_gap_adjusted_distances(
    timestamps_utc: Sequence[datetime],
    distances_m: Sequence[float | None],
    altitudes_m: Sequence[float | None],
) -> list[float]:
    """Per-interval grade-adjusted-equivalent flat distance (metres) for every `[i, i+1)`
    interval in the stream -- length `len(timestamps_utc) - 1`. This is the same per-interval
    quantity `compute_avg_gap_speed_mps` sums internally, extracted so a caller wanting the full
    per-interval series (`performance_curve.py`'s own sliding-window best-effort search, which
    needs a cumulative GAP-equivalent-distance array to search over) doesn't have to re-derive
    it. An interval contributes `0.0` -- never `None`, since a caller cumsum-ing this into a
    running total needs a concrete number, and never a fabricated positive distance either --
    whenever it's missing a distance/altitude reading, stationary
    (`_STATIONARY_MPS_FLOOR`), or its own `_windowed_grade` can't be computed -- the same skip
    conditions `compute_avg_gap_speed_mps`'s own summation loop applies below, now shared rather
    than duplicated. A valid interval's own contribution is always strictly positive (every
    grade-adjusted-time-factor this module computes is a positive multiplier), so `> 0.0` is an
    exact, not approximate, stand-in for "this interval was valid" wherever a caller needs that.
    """
    n = len(timestamps_utc)
    if len(distances_m) != n or len(altitudes_m) != n or n < 2:
        return []
    grades = _windowed_grades_batch(timestamps_utc, distances_m, altitudes_m)
    out: list[float] = []
    for i in range(n - 1):
        d0, d1 = distances_m[i], distances_m[i + 1]
        if d0 is None or d1 is None:
            out.append(0.0)
            continue
        dist_delta = d1 - d0
        if dist_delta <= 0:
            out.append(0.0)
            continue
        dt_s = (timestamps_utc[i + 1] - timestamps_utc[i]).total_seconds()
        if dt_s <= 0:
            out.append(0.0)
            continue
        speed_mps = dist_delta / dt_s
        if speed_mps < _STATIONARY_MPS_FLOOR:
            out.append(0.0)
            continue
        grade = grades[i]
        if grade is None:
            out.append(0.0)
            continue
        out.append(dist_delta / _grade_adjusted_time_factor(grade))
    return out


def compute_avg_gap_speed_mps(
    timestamps_utc: Sequence[datetime],
    distances_m: Sequence[float | None],
    altitudes_m: Sequence[float | None],
    *,
    start_idx: int = 0,
    end_idx: int | None = None,
    _per_interval: list[float] | None = None,
) -> float | None:
    """Equivalent-flat-distance average grade-adjusted speed across the whole stream:
    `total equivalent-flat distance / total moving time`, the same energy-conserving definition
    Strava/intervals.icu use. Each interval's own real distance is scaled into "how much distance
    this same effort would have covered on flat ground" by `1 / _grade_adjusted_time_factor`
    (the two are reciprocals -- see the derivation below), then summed and divided by total real
    moving time. Each interval's own grade comes from `_windowed_grade`, not its own two raw
    endpoints -- see module docstring for why a raw sample-to-sample grade is unusable.

    **This is not the same as time-weighting**, and the difference is not cosmetic -- it was a
    confirmed real bug, reported and diagnosed down to the exact mechanism by a careful reader
    who reproduced this project's own formula independently. An earlier version of this function
    instead scaled each interval's real *time* by `_grade_adjusted_time_factor` and divided total
    real distance by that summed grade-adjusted time. Per-interval, at constant speed, the two
    formulas are algebraically identical (either one reduces to `actual_pace *
    _grade_adjusted_time_factor(grade)` for a single uniform segment) -- which is exactly why
    every hand-crafted single-segment test already in this suite passed under both versions, and
    why the bug went undetected until a real multi-pace activity (fast reps interleaved with slow
    recovery jogs) exposed it. Once *aggregated* across segments of differing pace, the two
    formulas diverge: distance-weighting (this version) is
    `sum(dd_i / factor_i) / sum(dt_i)`, matching the standard physical
    derivation (constant power within each interval: energy_i = cost(grade_i) * distance_i,
    independent of how that interval's own speed varies) -- while time-weighting was
    `sum(dd_i) / sum(dt_i * factor_i)`, which has no equivalent physical justification once
    speed genuinely varies interval-to-interval. The wrong formula's error was also
    systematically one-directional (faster), not just noisier -- matching exactly what was
    reported -- because the cost curve is asymmetric around zero grade: confirmed directly
    against both branches (not the raw Minetti polynomial alone, which is uphill/flat-only and
    never runs downhill in this codebase), a 10% uphill costs about 2.37 J/(kg*m) more than
    flat, while the *softened* downhill branch (see `_grade_adjusted_time_factor`'s own docstring
    for why it's softened) only discounts a 10% downhill by about 0.43 J/(kg*m). A formula more
    sensitive to how *time* was distributed across segments -- recovery jogs, which are slow,
    contribute disproportionate time weight -- inherits more of that asymmetry than one weighted
    by real distance covered does.

    `start_idx`/`end_idx` restrict which raw intervals get *summed* (default: the whole stream,
    `[0, len-1)`) without restricting where `_windowed_grade` is allowed to look for its window
    edges -- callers pass the *full* stream's `distances_m`/`altitudes_m` even when only summing
    a sub-range (see `compute_lap_gap_speeds_mps`), so a point near the sub-range's own boundary
    still gets a real window on both sides instead of one truncated at that boundary.

    `_per_interval`, when given, must be `compute_gap_adjusted_distances`'s own output for this
    exact (unsliced) stream -- an internal-use-only escape hatch so a caller summing many
    sub-ranges of the *same* stream (`compute_lap_gap_speeds_mps`, one call per lap) can compute
    that per-interval array once and reuse it across every lap, rather than this function
    silently recomputing the full stream's own grade for every single lap call (an O(N) ->
    O(N x lap count) regression that would otherwise follow from a naive refactor). A whole-
    activity caller with nothing to reuse across calls (`refresh_avg_gap`) omits it and this
    function computes it fresh, same total cost as before this function existed.

    All three sequences must be the same length and share index order, as read straight off one
    Parquet table's columns. `None` when there's nothing usable to average (too few samples, or
    every interval missing a channel/stationary)."""
    n = len(timestamps_utc)
    if len(distances_m) != n or len(altitudes_m) != n or n < 2:
        return None
    # Clamped, not trusted verbatim -- a caller's own end_idx (e.g. compute_lap_gap_speeds_mps's
    # bisect_left against a lap start past the stream's last sample) can otherwise exceed n - 1
    # and index distances_m[i + 1] out of range below.
    end_idx = n - 1 if end_idx is None else min(end_idx, n - 1)
    start_idx = max(0, start_idx)

    per_interval = (
        _per_interval
        if _per_interval is not None
        else compute_gap_adjusted_distances(timestamps_utc, distances_m, altitudes_m)
    )

    total_moving_time_s = 0.0
    total_equiv_flat_m = 0.0
    for i in range(start_idx, end_idx):
        equiv_m = per_interval[i]
        if equiv_m <= 0.0:
            continue
        dt_s = (timestamps_utc[i + 1] - timestamps_utc[i]).total_seconds()
        total_equiv_flat_m += equiv_m
        total_moving_time_s += dt_s
    if total_moving_time_s <= 0 or total_equiv_flat_m <= 0:
        return None
    return total_equiv_flat_m / total_moving_time_s


def compute_lap_gap_speeds_mps(
    lap_start_epoch_s: Sequence[float],
    stream_epoch_s: Sequence[float],
    distances_m: Sequence[float | None],
    altitudes_m: Sequence[float | None],
) -> list[float | None]:
    """One average grade-adjusted speed per lap -- computed against each lap's own wall-clock
    boundary within the activity's full-resolution stream, reusing `compute_avg_gap_speed_mps`'s
    `start_idx`/`end_idx` bounds rather than a second averaging implementation. Same one-lap-is-
    one-time-range convention `frontend/src/gap.ts::computeLapGapsMinPerKm` used to -- a lap's own
    end is the next lap's start, or the stream's last point for the final lap -- except this is
    the real distance-weighted per-interval average (every stream sample within the lap
    contributes, matching `refresh_avg_gap`'s own whole-activity computation) rather than that
    (now-removed) frontend function's single net-elevation-change-over-the-whole-lap
    approximation, which is what actually let this move server-side: a headless caller can now
    read the same number the API already serves for a whole activity, per lap, without rendering
    a page.

    Deliberately passes the *full* stream to `compute_avg_gap_speed_mps` on every call, bounded
    by `start_idx`/`end_idx` rather than a sliced sub-array: `_windowed_grade` needs points
    outside a lap's own boundary to give a point near that boundary a real (not truncated)
    window -- slicing first was a confirmed real bug (a systematic fast bias right at interval
    boundaries, the exact place a coach most needs an accurate number).

    `lap_start_epoch_s`/`stream_epoch_s` are Unix seconds (epoch), not `datetime` -- the caller
    reads the Parquet stream via DuckDB's own `epoch(timestamp_utc)`, the same convention
    `transport_mix.py`/`stream_query.py` already use for a request-time Parquet read, so there's
    no tz-awareness mismatch to reconcile between a naive-implicit-UTC `lap.start_time_utc` and
    whatever tzinfo a Parquet timestamp round-trips as. `stream_epoch_s` must be sorted ascending
    (as read ordered by timestamp, matching every other reader of this file) so `bisect_left` can
    locate each lap's own boundary in it.

    All three stream sequences must be the same length and share index order. `None` per lap
    wherever that lap's own range has too few points, is missing a channel, or the lap falls
    entirely after the stream's last sample -- same contract as `compute_avg_gap_speed_mps` itself.
    """
    if (
        not stream_epoch_s
        or len(stream_epoch_s) != len(distances_m)
        or len(stream_epoch_s) != len(altitudes_m)
    ):
        return [None] * len(lap_start_epoch_s)

    timestamps_utc = [datetime.fromtimestamp(s, tz=UTC) for s in stream_epoch_s]
    # Computed once for the whole stream and reused across every lap below -- see
    # compute_avg_gap_speed_mps's own `_per_interval` docstring for why this matters: without it,
    # each of a real activity's ~10-20 laps would independently recompute _windowed_grade for
    # every sample in the *entire* stream, not just its own lap's share of it.
    per_interval = compute_gap_adjusted_distances(timestamps_utc, distances_m, altitudes_m)

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
        results.append(
            compute_avg_gap_speed_mps(
                timestamps_utc,
                distances_m,
                altitudes_m,
                start_idx=start_idx,
                end_idx=end_idx,
                _per_interval=per_interval,
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
