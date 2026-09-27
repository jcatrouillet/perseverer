"""A distance goal for a whole calendar year, month or 7-day week, and the progress line/trend
chart data computed against it -- see db/schema.py::goal for the storage shape (one row per
athlete per period, upserted, `sport=None` meaning every sport combined).

Progress is computed fresh on every read from the `activity` table, not cached in a rollup:
each query is already bounded to one year (<=366 days) or one month of an athlete's activities,
not the whole history, and a goal is only ever looked up once per page view (its own popup, not
rendered on every calendar page) -- a materialized rollup would be premature here.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Connection, func, select

from perseverer.db.schema import activity


class InvalidPeriod(ValueError):
    """Raised for a period_type/period_start combination that isn't a real calendar period --
    never silently clamped or guessed at, since a bad period would otherwise produce a
    plausible-looking but wrong progress line."""


def period_bounds(period_type: str, period_start: str) -> tuple[date, date]:
    """Returns (first_day, last_day), both inclusive, for a goal's period. `period_start` is
    "YYYY" for period_type="year", "YYYY-MM" for period_type="month", and the ISO date the 7-day
    period begins on for period_type="week" (any weekday -- the frontend's own week-start
    preference decides which, so a Sunday-start week is as valid as a Monday-start one)."""
    if period_type == "week":
        try:
            first = date.fromisoformat(period_start)
        except ValueError as e:
            raise InvalidPeriod(f"not a valid YYYY-MM-DD week start: {period_start!r}") from e
        return first, first + timedelta(days=6)
    if period_type == "year":
        try:
            year = int(period_start)
        except ValueError as e:
            raise InvalidPeriod(f"not a valid year: {period_start!r}") from e
        return date(year, 1, 1), date(year, 12, 31)
    if period_type == "month":
        try:
            year_str, month_str = period_start.split("-")
            year, month = int(year_str), int(month_str)
        except ValueError as e:
            raise InvalidPeriod(f"not a valid YYYY-MM month: {period_start!r}") from e
        if not 1 <= month <= 12:
            raise InvalidPeriod(f"not a valid YYYY-MM month: {period_start!r}")
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_day)
    raise InvalidPeriod(f"period_type must be 'week', 'month' or 'year', got {period_type!r}")


def repeated_week_starts(first_week_start: str, weeks: int) -> list[str]:
    """`weeks` consecutive 7-day periods beginning on `first_week_start` (an ISO date), each as
    that period's own ISO start date -- what "repeat this weekly goal for N weeks" expands to.
    The first entry is `first_week_start` itself."""
    first = period_bounds("week", first_week_start)[0]
    return [(first + timedelta(days=7 * i)).isoformat() for i in range(weeks)]


@dataclass
class DailyPoint:
    local_date: str
    cumulative_distance_m: float


@dataclass
class GoalProgress:
    period_end: str
    daily: list[DailyPoint]
    target_per_day_m: float
    current_distance_m: float
    target_distance_as_of_today_m: float
    ahead_behind_m: float
    pct_complete: float


def compute_progress(
    conn: Connection,
    *,
    athlete_id: str,
    period_type: str,
    period_start: str,
    sport: str | None,
    target_distance_m: float,
    as_of: date | None = None,
) -> GoalProgress:
    """Pure-ish (one query) progress computation: a day-by-day cumulative-distance line for the
    goal's sport (or every sport, if `sport` is None) across its period, plus where a dead-even
    straight-line pace would put you "as of today" -- the same "ahead/behind" framing the
    reference SPI/Strava goal widget uses. `as_of` defaults to real today; a caller-supplied
    value exists for deterministic testing, not for backdating a real goal's progress.
    """
    period_first, period_last = period_bounds(period_type, period_start)
    # Matches insights/engine.py's own "today" convention (activity.local_date is itself
    # offset-adjusted, ADR 0009 decision 8, but not per-athlete-timezone-aware at the instant
    # this function runs -- UTC-today is the same pragmatic anchor used there).
    today = as_of or datetime.now(UTC).date()
    # Actual data can't extend past today, and never before the period even started.
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
        select(activity.c.local_date, func.sum(activity.c.distance_m).label("distance_m"))
        .where(*filters)
        .group_by(activity.c.local_date)
        .order_by(activity.c.local_date)
    ).fetchall()
    by_date = {r.local_date: (r.distance_m or 0.0) for r in rows}

    total_days = (period_last - period_first).days + 1
    target_per_day_m = target_distance_m / total_days

    daily: list[DailyPoint] = []
    cumulative = 0.0
    d = period_first
    while d <= data_through:
        cumulative += by_date.get(d.isoformat(), 0.0)
        daily.append(DailyPoint(local_date=d.isoformat(), cumulative_distance_m=cumulative))
        d = date.fromordinal(d.toordinal() + 1)

    current_distance_m = daily[-1].cumulative_distance_m if daily else 0.0
    # 0, not negative, when the period hasn't started yet (a goal set in advance for a future
    # year/month) -- there's no sense in which "as of today" pace is behind before day one.
    days_elapsed = max(0, (data_through - period_first).days + 1)
    target_distance_as_of_today_m = target_per_day_m * days_elapsed

    return GoalProgress(
        period_end=period_last.isoformat(),
        daily=daily,
        target_per_day_m=target_per_day_m,
        current_distance_m=current_distance_m,
        target_distance_as_of_today_m=target_distance_as_of_today_m,
        ahead_behind_m=current_distance_m - target_distance_as_of_today_m,
        pct_complete=(current_distance_m / target_distance_m) if target_distance_m > 0 else 0.0,
    )
