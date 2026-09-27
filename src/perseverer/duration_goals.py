"""Duration goals -- a target amount of TIME spent on one sport (or on every sport combined) in a
week, month or year, and the progress line computed against it. Works for any sport at all, since
every activity has a duration -- unlike a distance goal (goals.py), which only makes sense for a
sport that covers a distance. See db/schema.py::duration_goal for the storage shape.

Time is the activity's MOVING duration (`moving_duration_s`), falling back to its elapsed
`duration_s` when the recording has no moving time -- the same "Moving time" figure the app's own
weekly/monthly/yearly stat tiles show, so a goal never disagrees with the totals next to it.
Progress is computed on read from `activity`, bounded to the goal's own period, never stored --
same shape as goals.py and bouldering_goals.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Connection, func, select

from perseverer.db.schema import activity
from perseverer.goals import period_bounds


@dataclass
class DailyDuration:
    local_date: str
    cumulative_duration_s: float


@dataclass
class DurationGoalProgress:
    period_end: str
    daily: list[DailyDuration]
    target_per_day_s: float
    current_duration_s: float
    target_as_of_today_s: float
    ahead_behind_s: float
    pct_complete: float


def compute_progress(
    conn: Connection,
    *,
    athlete_id: str,
    period_type: str,
    period_start: str,
    sport: str | None,
    target_duration_s: float,
    as_of: date | None = None,
) -> DurationGoalProgress:
    """A day-by-day cumulative-time line for the goal's sport (every sport if `sport` is None)
    across its period up to today, plus where a dead-even straight-line pace would put you "as of
    today" -- the same ahead/behind framing goals.py uses for distance. `as_of` defaults to real
    (UTC) today; a caller-supplied value exists for deterministic testing only."""
    period_first, period_last = period_bounds(period_type, period_start)
    today = as_of or datetime.now(UTC).date()
    data_through = min(today, period_last)

    filters = [
        activity.c.athlete_id == athlete_id,
        activity.c.deleted_at.is_(None),
        activity.c.local_date >= period_first.isoformat(),
        activity.c.local_date <= data_through.isoformat(),
    ]
    if sport is not None:
        filters.append(activity.c.sport == sport)

    rows = conn.execute(
        select(
            activity.c.local_date,
            func.sum(func.coalesce(activity.c.moving_duration_s, activity.c.duration_s)).label(
                "seconds"
            ),
        )
        .where(*filters)
        .group_by(activity.c.local_date)
    ).fetchall()
    by_date = {r.local_date: float(r.seconds or 0.0) for r in rows}

    total_days = (period_last - period_first).days + 1
    target_per_day_s = target_duration_s / total_days

    daily: list[DailyDuration] = []
    cumulative = 0.0
    d = period_first
    while d <= data_through:
        cumulative += by_date.get(d.isoformat(), 0.0)
        daily.append(DailyDuration(local_date=d.isoformat(), cumulative_duration_s=cumulative))
        d += timedelta(days=1)

    current = daily[-1].cumulative_duration_s if daily else 0.0
    # 0, not negative, before the period starts (a goal set in advance) -- same as goals.py.
    days_elapsed = max(0, (data_through - period_first).days + 1)
    target_as_of_today = target_per_day_s * days_elapsed

    return DurationGoalProgress(
        period_end=period_last.isoformat(),
        daily=daily,
        target_per_day_s=target_per_day_s,
        current_duration_s=current,
        target_as_of_today_s=target_as_of_today,
        ahead_behind_s=current - target_as_of_today,
        pct_complete=(current / target_duration_s) if target_duration_s > 0 else 0.0,
    )
