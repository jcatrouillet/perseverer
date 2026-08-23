"""Detects a hiking/walking recording that likely includes a stretch of car travel the athlete
forgot to stop tracking for -- confirmed against a real activity
(`01M0PY94N9J7XM1H9VPYBHME3T`, "Santa Cruz County Hiking"): 28 genuine hiking minutes at
0.3-1.7 m/s, then a sudden jump to 14-30+ m/s for the remaining 64 minutes and 49 of the
activity's 51.5 total km -- unmistakably driving. Scoped to `sport in {"hiking", "walking"}`,
where sustained fast movement is never legitimate (unlike running/cycling, where a fast finish
or a descent would false-positive).

Pure function over the activity's own per-second Parquet stream -- no DB writes, no persisted
flag. Recomputed fresh on every activity-detail request rather than stored, since it's cheap for
one activity's own Parquet file but deliberately never run list-wide (see
`api/routers/activities.py::get_activity`'s own docstring for why).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path

import duckdb

ELIGIBLE_SPORTS = frozenset({"hiking", "walking"})

# Comfortably above brisk-hike pace (~2.2 m/s / 8 km/h); comfortably below any plausible drive.
_FAST_SPEED_MPS = 3.0
# Genuinely back to walking pace, not just a momentary GPS speed blip.
_SLOW_SPEED_MPS = 1.5
# How long a fast stretch must sustain to count as "driving" rather than a brief downhill jog or
# a GPS speed spike -- long enough to rule out noise, short enough to still catch a short drive.
_SUSTAINED_WINDOW_S = 90.0
# The window used when walking the boundary in to find the actual suggested cut point. Deliberately
# the *same* length as _SUSTAINED_WINDOW_S, not shorter: a shorter window risks stopping at a
# brief lull inside the fast segment itself (a stop sign, a red light) rather than its genuine
# end, misreading a momentary dip as the return to hiking pace. Using the same window also
# guarantees the search can never falsely stop at t=0 -- the very definition of `at_start` is
# that the forward _SUSTAINED_WINDOW_S average there is fast, not slow.
_SETTLE_WINDOW_S = _SUSTAINED_WINDOW_S


@dataclass(frozen=True)
class TransportMixFlag:
    at_start: bool
    at_end: bool
    suggested_trim_start_s: float | None  # elapsed seconds from the activity's own start
    suggested_trim_end_s: float | None  # elapsed seconds from the activity's own start


def _avg_speed(
    elapsed_s: list[float], speed: list[float | None], lo: float, hi: float
) -> float | None:
    """Average speed over `elapsed_s` in `[lo, hi]`, using `bisect` for the index range since
    `elapsed_s` is already timestamp-sorted -- avoids an O(n) rescan of the whole stream for
    every window, which matters here since the caller checks one window per point."""
    i = bisect_left(elapsed_s, lo)
    j = bisect_right(elapsed_s, hi)
    values = [s for s in speed[i:j] if s is not None]
    if not values:
        return None
    return sum(values) / len(values)


def detect_transport_mix(
    con: duckdb.DuckDBPyConnection, parquet_path: Path, *, sport: str
) -> TransportMixFlag | None:
    """Returns `None` for an ineligible sport, an activity too short to judge, or one with no
    sustained fast segment touching either boundary. Otherwise flags which boundary (or both)
    and a suggested cut point: for the start, the first moment the trailing pace genuinely
    settles back under walking speed; for the end, the last moment before the pace climbs into
    the fast segment approaching the recording's end."""
    if sport not in ELIGIBLE_SPORTS:
        return None

    rows = con.execute(
        "SELECT epoch(timestamp_utc) AS ts, speed_mps FROM read_parquet(?) ORDER BY timestamp_utc",
        [str(parquet_path)],
    ).fetchall()
    if len(rows) < 2:
        return None

    t0 = rows[0][0]
    elapsed_s = [r[0] - t0 for r in rows]
    speed: list[float | None] = [r[1] for r in rows]
    total_s = elapsed_s[-1]
    if total_s < _SUSTAINED_WINDOW_S:
        return None

    at_start = (_avg_speed(elapsed_s, speed, 0.0, _SUSTAINED_WINDOW_S) or 0.0) > _FAST_SPEED_MPS
    at_end = (
        _avg_speed(elapsed_s, speed, total_s - _SUSTAINED_WINDOW_S, total_s) or 0.0
    ) > _FAST_SPEED_MPS
    if not at_start and not at_end:
        return None

    # Both suggestions scan forward from t=0 and stop at the *first* individual point whose own
    # speed crosses the threshold, confirmed by a forward window average also crossing it (to
    # rule out a single erroneous GPS reading rather than a genuine sustained transition) --
    # anchoring the trigger to the individual point, not the window average itself, is what puts
    # the suggested cut right at the true transition rather than up to a full window-length
    # early (a window average starts drifting the moment *any* fast sample enters it, well
    # before the transition itself when the speed differential is large). Scanning forward and
    # stopping immediately also means a noisy tail (stop-and-go arrival traffic, parking) can
    # never fool suggested_trim_end_s -- it's never even reached once the real boundary is found.
    suggested_trim_start_s = None
    if at_start:
        for t, s in zip(elapsed_s, speed, strict=True):
            window_avg = _avg_speed(elapsed_s, speed, t, t + _SETTLE_WINDOW_S) or 0.0
            if s is not None and s < _SLOW_SPEED_MPS and window_avg < _SLOW_SPEED_MPS:
                suggested_trim_start_s = t
                break

    suggested_trim_end_s = None
    if at_end:
        for t, s in zip(elapsed_s, speed, strict=True):
            window_avg = _avg_speed(elapsed_s, speed, t, t + _SETTLE_WINDOW_S) or 0.0
            if s is not None and s > _FAST_SPEED_MPS and window_avg > _FAST_SPEED_MPS:
                suggested_trim_end_s = t
                break

    return TransportMixFlag(
        at_start=at_start,
        at_end=at_end,
        suggested_trim_start_s=suggested_trim_start_s,
        suggested_trim_end_s=suggested_trim_end_s,
    )
