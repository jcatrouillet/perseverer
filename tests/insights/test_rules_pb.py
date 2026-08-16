from __future__ import annotations

import datetime as dt

from sporthealth.insights.rules_pb import compute_pb_insights
from sporthealth.insights.types import InsightActivity


def _activity(
    id_: str, local_date: str, distance_m: float, duration_s: float, sport_family: str = "run"
) -> InsightActivity:
    return InsightActivity(
        id=id_,
        start_time_utc=dt.datetime.fromisoformat(local_date + "T08:00:00"),
        local_date=local_date,
        sport="running",
        sport_family=sport_family,
        name=None,
        distance_m=distance_m,
        duration_s=duration_s,
        moving_duration_s=duration_s,
        avg_hr=None,
        max_hr=None,
        cadence=None,
        elevation_gain_m=None,
        elevation_loss_m=None,
        temperature_min_c=None,
        temperature_max_c=None,
    )


def test_all_time_5k_best_is_flagged_when_still_the_window_best() -> None:
    activities = [
        _activity("slow", "2026-01-01", 5000.0, 1600.0),
        _activity("fastest", "2026-08-10", 5000.0, 1200.0),
    ]
    insights = compute_pb_insights(activities, dt.date(2026, 8, 14))
    pb = [i for i in insights if i.subject_key == "pb:run:5 km" and i.window == "30d"]
    assert len(pb) == 1
    assert pb[0].activity_id == "fastest"
    assert pb[0].value_num == 1200.0


def test_old_pb_outside_window_is_not_flagged_for_that_window() -> None:
    activities = [_activity("old_pb", "2026-01-01", 5000.0, 1200.0)]
    insights = compute_pb_insights(activities, dt.date(2026, 8, 14))
    assert not any(i.subject_key == "pb:run:5 km" and i.window == "30d" for i in insights)
    # But it's still the all-time best, so the 365d window (which includes Jan 1) should show it.
    assert any(i.subject_key == "pb:run:5 km" and i.window == "365d" for i in insights)


def test_window_best_that_is_not_the_all_time_best_is_not_flagged() -> None:
    activities = [
        _activity("all_time_best", "2026-06-01", 5000.0, 1100.0),
        _activity("recent_but_slower", "2026-08-10", 5000.0, 1300.0),
    ]
    insights = compute_pb_insights(activities, dt.date(2026, 8, 14))
    thirty_day = [i for i in insights if i.subject_key == "pb:run:5 km" and i.window == "30d"]
    assert thirty_day == []  # the 30d window's own best (1300s) isn't the all-time best


def test_distance_outside_tolerance_band_is_not_eligible() -> None:
    activities = [_activity("way_off", "2026-08-10", 5000.0 * 1.5, 2000.0)]
    insights = compute_pb_insights(activities, dt.date(2026, 8, 14))
    assert not any(i.subject_key == "pb:run:5 km" for i in insights)


def test_pb_insights_are_scoped_per_sport_family() -> None:
    activities = [
        _activity("run5k", "2026-08-10", 5000.0, 1200.0, sport_family="run"),
        _activity("ride5k", "2026-08-11", 5000.0, 600.0, sport_family="ride"),
    ]
    insights = compute_pb_insights(activities, dt.date(2026, 8, 14))
    run_pb = next(i for i in insights if i.subject_key == "pb:run:5 km" and i.window == "30d")
    ride_pb = next(i for i in insights if i.subject_key == "pb:ride:5 km" and i.window == "30d")
    assert run_pb.activity_id == "run5k"
    assert ride_pb.activity_id == "ride5k"
