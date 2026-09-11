"""A single upcoming race on the calendar (`planned_race`) -- a dated event with a distance and
an optional target finish time, deliberately not a `planned_workout` sport tier: a race has no
step model to push to Garmin, it's just something to look forward to and pace a goal against.

The one piece of real logic here is `predicted_duration_s_for_distance`: comparing the athlete's
target against their *current* predicted finish time for that distance, reusing
`performance_daily_rollup`'s own independently-computed race predictions
(`performance_rollup.py`) rather than inventing a second prediction path. Only works for the
four standard distances `vdot.RACE_DISTANCES_M` already covers (5k/10k/half/marathon,
`predict_race_time_s`'s own search-bound limitation) -- a custom distance (a 15k, a 50-miler)
just shows its target with no prediction, which is honest rather than extrapolated.
"""

from __future__ import annotations

from sqlalchemy import Connection, desc, select

from perseverer.db.schema import performance_daily_rollup
from perseverer.vdot import RACE_DISTANCES_M

# A race distance is entered by the athlete (a preset dropdown for the four standard ones, or a
# free km number for "Custom") -- a small tolerance absorbs float round-tripping through km<->m
# without accidentally matching a genuinely different custom distance to a standard one.
_DISTANCE_MATCH_TOLERANCE_M = 1.0


def _matching_standard_label(distance_m: float) -> str | None:
    for label, standard_m in RACE_DISTANCES_M.items():
        if abs(distance_m - standard_m) <= _DISTANCE_MATCH_TOLERANCE_M:
            return label
    return None


def predicted_duration_s_for_distance(
    conn: Connection, *, athlete_id: str, distance_m: float
) -> float | None:
    """The athlete's most recent predicted finish time for `distance_m`, or `None` when it isn't
    one of the four standard race distances, or no performance rollup has ever run yet."""
    label = _matching_standard_label(distance_m)
    if label is None:
        return None
    column = performance_daily_rollup.c[f"predicted_{label}_s"]
    row = conn.execute(
        select(column)
        .where(
            performance_daily_rollup.c.athlete_id == athlete_id,
            column.is_not(None),
        )
        .order_by(desc(performance_daily_rollup.c.local_date))
        .limit(1)
    ).fetchone()
    return row[0] if row is not None else None
