from __future__ import annotations

import datetime as dt

from perseverer.insights.rules_streaks import compute_streak_insights
from perseverer.insights.types import InsightActivity


def _activity(id_: str, local_date: str) -> InsightActivity:
    return InsightActivity(
        id=id_,
        start_time_utc=dt.datetime.fromisoformat(local_date + "T08:00:00"),
        local_date=local_date,
        sport="running",
        sport_family="run",
        name=None,
        distance_m=5000.0,
        duration_s=1500.0,
        moving_duration_s=1500.0,
        avg_hr=None,
        max_hr=None,
        cadence=None,
        max_cadence=None,
        elevation_gain_m=None,
        elevation_loss_m=None,
        max_altitude_m=None,
        temperature_min_c=None,
        temperature_max_c=None,
    )


def test_current_streak_counts_consecutive_days_ending_today() -> None:
    activities = [
        _activity("a1", "2026-08-12"),
        _activity("a2", "2026-08-13"),
        _activity("a3", "2026-08-14"),
    ]
    insights = compute_streak_insights(activities, dt.date(2026, 8, 14))
    current = next(i for i in insights if i.subject_key == "streak:current")
    assert current.value_num == 3.0


def test_current_streak_is_zero_when_nothing_today() -> None:
    activities = [_activity("a1", "2026-08-10")]
    insights = compute_streak_insights(activities, dt.date(2026, 8, 14))
    assert not any(i.subject_key == "streak:current" for i in insights)


def test_longest_streak_in_window_finds_the_best_run_not_just_the_latest() -> None:
    activities = [
        _activity("a1", "2026-08-01"),
        _activity("a2", "2026-08-02"),
        _activity("a3", "2026-08-03"),
        _activity("a4", "2026-08-04"),
        _activity("a5", "2026-08-10"),  # isolated, breaks the streak
    ]
    insights = compute_streak_insights(activities, dt.date(2026, 8, 14))
    longest = next(i for i in insights if i.window == "30d" and i.subject_key == "streak:longest")
    assert longest.value_num == 4.0
    assert longest.detail["end"] == "2026-08-04"


def test_longest_rest_gap_reports_the_return_activity() -> None:
    activities = [
        _activity("before", "2026-08-01"),
        _activity("after", "2026-08-09"),  # 7-day gap
    ]
    insights = compute_streak_insights(activities, dt.date(2026, 8, 14))
    gap = next(i for i in insights if i.window == "30d" and i.subject_key == "streak:longest_rest")
    assert gap.value_num == 7.0
    assert gap.activity_id == "after"
    assert gap.detail["returned"] == "2026-08-09"


def test_single_activity_produces_no_streak_or_gap_insight() -> None:
    activities = [_activity("only", "2026-08-10")]
    insights = compute_streak_insights(activities, dt.date(2026, 8, 14))
    window_insights = [i for i in insights if i.window == "30d"]
    assert window_insights == []
