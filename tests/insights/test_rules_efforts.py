from __future__ import annotations

import datetime as dt

from perseverer.insights.rules_efforts import compute_effort_insights
from perseverer.insights.types import InsightActivity


def _activity(
    id_: str,
    local_date: str,
    *,
    sport: str = "running",
    sport_family: str = "run",
    distance_m: float | None = 5000.0,
    duration_s: float | None = 1500.0,
    avg_hr: float | None = None,
    max_hr: float | None = None,
    cadence: float | None = None,
    max_cadence: float | None = None,
    elevation_gain_m: float | None = None,
    elevation_loss_m: float | None = None,
    temperature_min_c: float | None = None,
    temperature_max_c: float | None = None,
    hour: int = 8,
    utc_offset_s: int = 0,
) -> InsightActivity:
    return InsightActivity(
        id=id_,
        start_time_utc=dt.datetime.fromisoformat(local_date + f"T{hour:02d}:00:00"),
        utc_offset_s=utc_offset_s,
        local_date=local_date,
        sport=sport,
        sport_family=sport_family,
        name=None,
        distance_m=distance_m,
        duration_s=duration_s,
        moving_duration_s=duration_s,
        avg_hr=avg_hr,
        max_hr=max_hr,
        cadence=cadence,
        max_cadence=max_cadence,
        elevation_gain_m=elevation_gain_m,
        elevation_loss_m=elevation_loss_m,
        temperature_min_c=temperature_min_c,
        temperature_max_c=temperature_max_c,
    )


def test_longest_distance_within_window_wins() -> None:
    activities = [
        _activity("a1", "2026-08-01", distance_m=5000.0),
        _activity("a2", "2026-08-05", distance_m=10000.0),
        _activity("a3", "2026-08-10", distance_m=3000.0),
    ]
    insights = compute_effort_insights(activities, dt.date(2026, 8, 14))
    thirty_day = [i for i in insights if i.window == "30d" and i.subject_key == "distance:run"]
    assert len(thirty_day) == 1
    assert thirty_day[0].activity_id == "a2"
    assert thirty_day[0].value_num == 10000.0


def test_activities_outside_window_are_excluded() -> None:
    activities = [
        _activity("old", "2026-01-01", distance_m=50000.0),
        _activity("recent", "2026-08-10", distance_m=5000.0),
    ]
    insights = compute_effort_insights(activities, dt.date(2026, 8, 14))
    thirty_day = [i for i in insights if i.window == "30d" and i.subject_key == "distance:run"]
    assert thirty_day[0].activity_id == "recent"  # the 50km outlier is outside the 30d window


def test_sport_scoped_dimension_produces_one_insight_per_family() -> None:
    activities = [
        _activity("run1", "2026-08-10", sport="running", sport_family="run", distance_m=8000.0),
        _activity("ride1", "2026-08-11", sport="cycling", sport_family="ride", distance_m=30000.0),
    ]
    insights = compute_effort_insights(activities, dt.date(2026, 8, 14))
    distance_insights = [
        i for i in insights if i.window == "30d" and i.subject_key.startswith("distance:")
    ]
    families = {i.sport_family for i in distance_insights}
    assert families == {"run", "ride"}


def test_pace_direction_is_low_fastest_wins() -> None:
    activities = [
        _activity("slow", "2026-08-10", distance_m=5000.0, duration_s=1800.0),  # 6:00/km
        _activity("fast", "2026-08-11", distance_m=5000.0, duration_s=1200.0),  # 4:00/km
    ]
    insights = compute_effort_insights(activities, dt.date(2026, 8, 14))
    pace = [i for i in insights if i.window == "30d" and i.subject_key == "pace:run"]
    assert pace[0].activity_id == "fast"


def test_avg_hr_high_and_low_are_distinct_insights() -> None:
    activities = [
        _activity("low_hr", "2026-08-10", avg_hr=110.0),
        _activity("high_hr", "2026-08-11", avg_hr=170.0),
    ]
    insights = compute_effort_insights(activities, dt.date(2026, 8, 14))
    by_key = {i.subject_key: i for i in insights if i.window == "30d"}
    assert by_key["avg_hr_high:run"].activity_id == "high_hr"
    assert by_key["avg_hr_low:run"].activity_id == "low_hr"


def test_max_cadence_is_a_distinct_dimension_from_avg_cadence() -> None:
    activities = [
        _activity("high_avg", "2026-08-10", cadence=170.0, max_cadence=178.0),
        _activity("high_max_only", "2026-08-11", cadence=165.0, max_cadence=185.0),
    ]
    insights = compute_effort_insights(activities, dt.date(2026, 8, 14))
    by_key = {i.subject_key: i for i in insights if i.window == "30d"}
    assert by_key["cadence:run"].activity_id == "high_avg"
    assert by_key["max_cadence:run"].activity_id == "high_max_only"
    # Distinct titles -- "Highest cadence" alone would be ambiguous now that both an avg-cadence
    # and a max-cadence dimension exist side by side.
    assert by_key["cadence:run"].title == "Highest average cadence (run)"
    assert by_key["max_cadence:run"].title == "Highest max cadence (run)"


def test_non_sport_scoped_dimension_ignores_sport_family() -> None:
    activities = [
        _activity("morning", "2026-08-10", sport="running", sport_family="run", hour=6),
        _activity("evening", "2026-08-11", sport="cycling", sport_family="ride", hour=20),
    ]
    insights = compute_effort_insights(activities, dt.date(2026, 8, 14))
    earliest = [i for i in insights if i.window == "30d" and i.subject_key == "start_earliest"]
    latest = [i for i in insights if i.window == "30d" and i.subject_key == "start_latest"]
    assert len(earliest) == 1  # not split by sport family
    assert earliest[0].activity_id == "morning"
    assert latest[0].activity_id == "evening"


def test_start_hour_dimensions_compare_local_time_not_raw_utc() -> None:
    # Real-data regression: a run stored at 05:00 UTC but UTC-7 (so actually 22:00 local, a late
    # evening run) must NOT win "earliest start" against a genuine 06:00-local morning run just
    # because its raw UTC hour happens to be smaller. Comparing raw UTC hours (the pre-fix
    # behaviour) let exactly this kind of activity masquerade as the day's earliest.
    activities = [
        _activity("late_local_small_utc_hour", "2026-08-10", hour=5, utc_offset_s=-7 * 3600),
        _activity("genuinely_early_local", "2026-08-11", hour=6, utc_offset_s=0),
    ]
    insights = compute_effort_insights(activities, dt.date(2026, 8, 14))
    earliest = next(i for i in insights if i.window == "30d" and i.subject_key == "start_earliest")
    assert earliest.activity_id == "genuinely_early_local"


def test_no_data_for_a_dimension_produces_no_insight() -> None:
    activities = [_activity("a1", "2026-08-10", avg_hr=None, max_hr=None)]
    insights = compute_effort_insights(activities, dt.date(2026, 8, 14))
    assert not any(i.subject_key.startswith("avg_hr_high") for i in insights)


def test_calendar_year_window_only_includes_this_year() -> None:
    activities = [
        _activity("last_year", "2025-12-31", distance_m=42000.0),
        _activity("this_year", "2026-01-05", distance_m=5000.0),
    ]
    insights = compute_effort_insights(activities, dt.date(2026, 8, 14))
    year_insight = [i for i in insights if i.window == "year" and i.subject_key == "distance:run"]
    assert year_insight[0].activity_id == "this_year"
