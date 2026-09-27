"""Bouldering goals -- a target NUMBER OF COMPLETED ROUTES in a week, month or year, optionally
at one V-grade (or that grade and harder), and the progress line computed against it. See
db/schema.py::bouldering_goal for the storage shape and why several goals may share a period.

Only completed routes ("sends") count toward a goal -- an attempted-but-not-completed route
(`split.climb_result != "completed"`, including an unconfirmed "unknown_<n>" raw value, which the
FIT parser conservatively treats as an attempt) never does. Progress is computed fresh on every
read from `split` joined to its bouldering `activity`, bounded to the goal's own period, so a
corrected route status (bouldering_overrides.py) is reflected immediately with nothing to keep in
sync -- same "computed on read, not stored" shape as goals.py's distance goals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Connection, select

from perseverer.db.schema import activity
from perseverer.db.schema import split as split_table
from perseverer.goals import InvalidPeriod
from perseverer.goals import period_bounds as distance_period_bounds


def period_bounds(period_type: str, period_start: str) -> tuple[date, date]:
    """(first_day, last_day), both inclusive. Year/month reuse goals.py's own parsing; a week is
    the 7 days beginning on the ISO date `period_start` (any weekday)."""
    if period_type == "week":
        try:
            first = date.fromisoformat(period_start)
        except ValueError as e:
            raise InvalidPeriod(f"not a valid YYYY-MM-DD week start: {period_start!r}") from e
        return first, first + timedelta(days=6)
    if period_type in ("year", "month"):
        return distance_period_bounds(period_type, period_start)
    raise InvalidPeriod(f"period_type must be 'week', 'month' or 'year', got {period_type!r}")


def grade_matches(climb_grade: int, goal_grade: int | None, and_harder: bool) -> bool:
    """Whether a route of `climb_grade` counts toward a goal for `goal_grade` (None = any)."""
    if goal_grade is None:
        return True
    return climb_grade >= goal_grade if and_harder else climb_grade == goal_grade


@dataclass
class DailyCount:
    local_date: str
    cumulative_count: int


@dataclass
class BoulderingGoalProgress:
    period_end: str
    daily: list[DailyCount]
    target_per_day: float
    current_count: int
    target_as_of_today: float
    ahead_behind: float
    pct_complete: float


def compute_progress(
    conn: Connection,
    *,
    athlete_id: str,
    period_type: str,
    period_start: str,
    grade: int | None,
    and_harder: bool,
    target_count: int,
    as_of: date | None = None,
) -> BoulderingGoalProgress:
    """A day-by-day cumulative count of completed matching routes across the goal's period up to
    today, plus where a dead-even straight-line pace would put you "as of today" -- the same
    ahead/behind framing goals.py uses for distance goals. `as_of` defaults to real (UTC) today;
    a caller-supplied value exists for deterministic testing only."""
    period_first, period_last = period_bounds(period_type, period_start)
    today = as_of or datetime.now(UTC).date()
    data_through = min(today, period_last)

    rows = conn.execute(
        select(activity.c.local_date, split_table.c.climb_grade)
        .select_from(split_table.join(activity, activity.c.id == split_table.c.activity_id))
        .where(
            activity.c.athlete_id == athlete_id,
            activity.c.deleted_at.is_(None),
            activity.c.sub_sport == "bouldering",
            activity.c.local_date >= period_first.isoformat(),
            activity.c.local_date <= data_through.isoformat(),
            split_table.c.climb_grade.is_not(None),
            split_table.c.climb_result == "completed",
        )
    ).fetchall()
    by_date: dict[str, int] = {}
    for r in rows:
        if grade_matches(r.climb_grade, grade, and_harder):
            by_date[r.local_date] = by_date.get(r.local_date, 0) + 1

    total_days = (period_last - period_first).days + 1
    target_per_day = target_count / total_days

    daily: list[DailyCount] = []
    cumulative = 0
    d = period_first
    while d <= data_through:
        cumulative += by_date.get(d.isoformat(), 0)
        daily.append(DailyCount(local_date=d.isoformat(), cumulative_count=cumulative))
        d += timedelta(days=1)

    current = daily[-1].cumulative_count if daily else 0
    # 0, not negative, before the period starts (a goal set in advance) -- same as goals.py.
    days_elapsed = max(0, (data_through - period_first).days + 1)
    target_as_of_today = target_per_day * days_elapsed

    return BoulderingGoalProgress(
        period_end=period_last.isoformat(),
        daily=daily,
        target_per_day=target_per_day,
        current_count=current,
        target_as_of_today=target_as_of_today,
        ahead_behind=current - target_as_of_today,
        pct_complete=(current / target_count) if target_count > 0 else 0.0,
    )
