"""Streak & consistency insights: the current run of consecutive active days, the longest
streak within each of the standard windows, and the longest rest gap within each window (whose
detail already carries the "first activity back" date/activity -- a separate rule for that
would just restate the same event). Pure functions over `InsightActivity` lists, no DB access.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

from sporthealth.insights.rules_efforts import WINDOWS, window_start_date
from sporthealth.insights.types import Insight, InsightActivity


def _distinct_dates(activities: list[InsightActivity]) -> list[date]:
    return sorted({date.fromisoformat(a.local_date) for a in activities})


def _activity_for_date(activities: list[InsightActivity], d: date) -> InsightActivity | None:
    for a in activities:
        if a.local_date == d.isoformat():
            return a
    return None


def current_streak(activities: list[InsightActivity], as_of: date) -> Insight | None:
    active_dates = {date.fromisoformat(a.local_date) for a in activities}
    streak = 0
    d = as_of
    while d in active_dates:
        streak += 1
        d -= timedelta(days=1)
    if streak == 0:
        return None
    return Insight(
        kind="streak",
        window="current",
        subject_key="streak:current",
        title=f"{streak}-day activity streak",
        detail={"days": streak},
        value_num=float(streak),
        local_date=as_of.isoformat(),
    )


def _longest_consecutive_run(dates: list[date]) -> tuple[date, date, int] | None:
    if not dates:
        return None
    best_start, best_end, best_len = dates[0], dates[0], 1
    run_start, run_len = dates[0], 1
    for prev, cur in pairwise(dates):
        if (cur - prev).days == 1:
            run_len += 1
        else:
            run_start, run_len = cur, 1
        if run_len > best_len:
            best_len = run_len
            best_start = run_start
            best_end = cur
    return best_start, best_end, best_len


def _longest_gap(dates: list[date]) -> tuple[date, date, int] | None:
    if len(dates) < 2:
        return None
    best: tuple[date, date, int] | None = None
    for prev, cur in pairwise(dates):
        gap_days = (cur - prev).days - 1
        if gap_days > 0 and (best is None or gap_days > best[2]):
            best = (prev, cur, gap_days)
    return best


def window_streak_insights(
    activities: list[InsightActivity], window: str, days: int | None, as_of: date
) -> list[Insight]:
    start = window_start_date(window, days, as_of)
    windowed = [
        a for a in activities if start.isoformat() <= a.local_date <= as_of.isoformat()
    ]
    dates = _distinct_dates(windowed)
    insights: list[Insight] = []

    run = _longest_consecutive_run(dates)
    if run is not None and run[2] > 1:
        run_start, run_end, length = run
        insights.append(
            Insight(
                kind="streak",
                window=window,
                subject_key="streak:longest",
                title=f"{length}-day activity streak",
                detail={"start": run_start.isoformat(), "end": run_end.isoformat(), "days": length},
                value_num=float(length),
                local_date=run_end.isoformat(),
            )
        )

    gap = _longest_gap(dates)
    if gap is not None:
        gap_start, gap_end, gap_days = gap
        return_activity = _activity_for_date(windowed, gap_end)
        insights.append(
            Insight(
                kind="streak",
                window=window,
                subject_key="streak:longest_rest",
                title=f"{gap_days}-day rest, then back to it",
                detail={
                    "last_active_before": gap_start.isoformat(),
                    "returned": gap_end.isoformat(),
                    "rest_days": gap_days,
                    "activity_id": return_activity.id if return_activity else None,
                },
                value_num=float(gap_days),
                activity_id=return_activity.id if return_activity else None,
                local_date=gap_end.isoformat(),
            )
        )
    return insights


def compute_streak_insights(activities: list[InsightActivity], as_of: date) -> list[Insight]:
    insights: list[Insight] = []
    current = current_streak(activities, as_of)
    if current is not None:
        insights.append(current)
    for window, days in WINDOWS:
        insights.extend(window_streak_insights(activities, window, days, as_of))
    return insights
