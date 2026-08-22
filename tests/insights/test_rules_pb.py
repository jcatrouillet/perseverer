from __future__ import annotations

import datetime as dt

from perseverer.insights.rules_pb import compute_pb_insights, compute_window_best_insights
from perseverer.insights.types import InsightActivity


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
        max_cadence=None,
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


def test_a_different_whole_km_band_is_not_eligible() -> None:
    activities = [_activity("way_off", "2026-08-10", 7500.0, 2000.0)]  # floors to "7 km"
    insights = compute_pb_insights(activities, dt.date(2026, 8, 14))
    assert not any(i.subject_key == "pb:run:5 km" for i in insights)


def test_floor_km_banding_groups_5_01_and_5_99_together_but_not_4_99() -> None:
    # 5.01km and 5.99km both floor to the "5 km" band and compete against each other there
    # (5.99km's own runner is faster, so it's the one that surfaces); 4.99km floors to a
    # disjoint "4 km" band and never competes with either.
    low = _activity("low", "2026-08-10", 5010.0, 1500.0)  # 0.2994 s/m
    high = _activity("high", "2026-08-11", 5990.0, 1600.0)  # 0.2671 s/m -- faster
    just_under = _activity("just_under", "2026-08-12", 4990.0, 1400.0)
    insights = compute_pb_insights([low, high, just_under], dt.date(2026, 8, 14))
    five_km = next(i for i in insights if i.subject_key == "pb:run:5 km" and i.window == "30d")
    four_km = next(i for i in insights if i.subject_key == "pb:run:4 km" and i.window == "30d")
    assert five_km.activity_id == "high"  # the faster of the two 5km-band candidates
    assert four_km.activity_id == "just_under"


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


class TestComputeWindowBestInsights:
    """The deliberately weaker sibling of compute_pb_insights -- "fastest in this window" even
    when it isn't the athlete's all-time best in that band (see rules_pb.py's own docstring for
    the real-data scenario that motivated this: a recent 6:26/km run was the fastest ~6km effort
    in the last 30 days, but ranked ~20th within the last 365 days alone)."""

    def test_flagged_when_the_window_best_is_not_the_all_time_best(self) -> None:
        activities = [
            _activity("all_time_best", "2026-06-01", 5000.0, 1100.0),
            _activity("recent_but_slower", "2026-08-10", 5000.0, 1300.0),
        ]
        insights = compute_window_best_insights(activities, dt.date(2026, 8, 14))
        thirty_day = [
            i for i in insights if i.subject_key == "window_best:run:5 km" and i.window == "30d"
        ]
        assert len(thirty_day) == 1
        assert thirty_day[0].activity_id == "recent_but_slower"
        assert thirty_day[0].value_num == 1300.0

    def test_not_flagged_when_the_window_best_is_also_the_all_time_best(self) -> None:
        # This case is compute_pb_insights's job -- flagging it again here would be a redundant,
        # weaker echo of "all-time best" for the same activity.
        activities = [
            _activity("slow", "2026-01-01", 5000.0, 1600.0),
            _activity("fastest", "2026-08-10", 5000.0, 1200.0),
        ]
        insights = compute_window_best_insights(activities, dt.date(2026, 8, 14))
        assert not any(
            i.subject_key == "window_best:run:5 km" and i.window == "30d" for i in insights
        )

    def test_a_different_whole_km_band_is_not_eligible(self) -> None:
        activities = [_activity("way_off", "2026-08-10", 7500.0, 2000.0)]  # floors to "7 km"
        insights = compute_window_best_insights(activities, dt.date(2026, 8, 14))
        assert not any(i.subject_key == "window_best:run:5 km" for i in insights)

    def test_scoped_per_sport_family(self) -> None:
        activities = [
            _activity("run5k_old", "2026-01-01", 5000.0, 1100.0, sport_family="run"),
            _activity("run5k_recent", "2026-08-10", 5000.0, 1300.0, sport_family="run"),
            _activity("ride5k_old", "2026-01-02", 5000.0, 500.0, sport_family="ride"),
            _activity("ride5k_recent", "2026-08-11", 5000.0, 600.0, sport_family="ride"),
        ]
        insights = compute_window_best_insights(activities, dt.date(2026, 8, 14))
        run_wb = next(
            i for i in insights if i.subject_key == "window_best:run:5 km" and i.window == "30d"
        )
        ride_wb = next(
            i for i in insights if i.subject_key == "window_best:ride:5 km" and i.window == "30d"
        )
        assert run_wb.activity_id == "run5k_recent"
        assert ride_wb.activity_id == "ride5k_recent"

    def test_only_the_widest_still_true_window_stays_a_candidate_before_dedup(self) -> None:
        """rules_activity.py applies its own widest-window dedup on top of this function's raw
        output -- this test just confirms the raw output still has the shape that dedup expects
        (multiple window rows for the same subject_key, since 30d's winner is also 90d's winner
        here)."""
        activities = [
            _activity("all_time_best", "2025-01-01", 5000.0, 1100.0),
            _activity("recent_best", "2026-08-10", 5000.0, 1300.0),
        ]
        insights = compute_window_best_insights(activities, dt.date(2026, 8, 14))
        windows = {
            i.window for i in insights if i.subject_key == "window_best:run:5 km"
        }
        assert "30d" in windows
        assert "90d" in windows  # recent_best is still the only/fastest candidate at 90d too
