from __future__ import annotations

import datetime as dt

from perseverer.insights.rules_activity import compute_activity_insights
from perseverer.insights.types import InsightActivity


def _activity(
    id_: str,
    local_date: str,
    distance_m: float,
    duration_s: float,
    sport_family: str = "run",
    temperature_min_c: float | None = None,
    temperature_max_c: float | None = None,
    cadence: float | None = None,
    max_cadence: float | None = None,
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
        cadence=cadence,
        max_cadence=max_cadence,
        elevation_gain_m=None,
        elevation_loss_m=None,
        temperature_min_c=temperature_min_c,
        temperature_max_c=temperature_max_c,
    )


def test_never_credits_a_future_activity_for_a_past_ones_insight() -> None:
    # Caller-supplied "bounded" list already excludes the future PR -- this test asserts the
    # rule module doesn't need to (and can't) see it: the earlier activity still gets its own
    # (honest) "best 10K to date" credit regardless of what happens later.
    earlier = _activity("earlier", "2026-01-01", 10000.0, 3000.0)
    bounded = [earlier]  # the later, faster run is deliberately NOT included
    insights = compute_activity_insights("earlier", bounded, dt.date(2026, 1, 1))
    ten_k_pb = [i for i in insights if i.subject_key == "pb:run:10 km"]
    assert len(ten_k_pb) == 1
    assert ten_k_pb[0].activity_id == "earlier"


def test_activity_that_is_not_the_best_in_its_pool_gets_no_pb_insight() -> None:
    slower = _activity("slower", "2026-01-01", 10000.0, 3200.0)
    faster = _activity("faster", "2026-06-01", 10000.0, 3000.0)
    insights = compute_activity_insights("slower", [slower, faster], dt.date(2026, 6, 1))
    assert not any(i.kind == "pb" for i in insights)


def test_only_the_widest_matching_window_is_kept_for_a_repeated_extreme() -> None:
    target = _activity("solo", "2026-06-15", 42000.0, 15000.0)
    pad1 = _activity("pad1", "2026-01-01", 10000.0, 3600.0)
    pad2 = _activity("pad2", "2026-03-01", 10000.0, 3600.0)
    insights = compute_activity_insights("solo", [target, pad1, pad2], dt.date(2026, 6, 15))
    distance_insights = [i for i in insights if i.subject_key == "distance:run"]
    # This run is the longest in every window (30d/90d/180d/year/365d all trivially contain its
    # own date) -- only one card should survive, not five near-duplicates. The whole comparison
    # pool also fits inside 365d, so it's promoted all the way to the "ever" (all-time) label.
    assert len(distance_insights) == 1
    assert distance_insights[0].window == "all_time"


def test_current_streak_is_included_once_it_reaches_the_notable_threshold() -> None:
    activities = [
        _activity("d1", "2026-06-12", 5000.0, 1500.0),
        _activity("d2", "2026-06-13", 5000.0, 1500.0),
        _activity("d3", "2026-06-14", 5000.0, 1500.0),
        _activity("d4", "2026-06-15", 5000.0, 1500.0),
    ]
    insights = compute_activity_insights("d4", activities, dt.date(2026, 6, 15))
    streak = next(i for i in insights if i.kind == "streak")
    assert streak.value_num == 4.0
    assert streak.activity_id is None
    assert streak.title == "4-day run streak"


def test_streak_below_four_days_is_omitted_entirely() -> None:
    # A 1-, 2-, or 3-day "streak" is just restating "you ran today/recently" -- not a real
    # insight, so no streak card should appear at all below the notable threshold.
    activities = [
        _activity("d1", "2026-06-14", 5000.0, 1500.0),
        _activity("d2", "2026-06-15", 5000.0, 1500.0),
    ]
    insights = compute_activity_insights("d2", activities, dt.date(2026, 6, 15))
    assert not any(i.kind == "streak" for i in insights)


def test_streak_only_counts_running_days_not_other_sports() -> None:
    activities = [
        _activity("run1", "2026-06-13", 5000.0, 1500.0, sport_family="run"),
        _activity("yoga1", "2026-06-14", 0.0, 1800.0, sport_family="cardio"),
        _activity("run2", "2026-06-15", 5000.0, 1500.0, sport_family="run"),
    ]
    insights = compute_activity_insights("run2", activities, dt.date(2026, 6, 15))
    # A non-running activity on 06-14 must not bridge the streak -- only run1/run2 count, and
    # they aren't consecutive days for the *running* streak (there's a run-less day between),
    # so the resulting 1-day streak falls below the notable threshold and isn't shown.
    assert not any(i.kind == "streak" for i in insights)


def test_effort_and_pb_titles_state_the_covered_period() -> None:
    target = _activity("solo", "2026-06-15", 42000.0, 15000.0)
    pad1 = _activity("pad1", "2026-01-01", 10000.0, 3600.0)
    pad2 = _activity("pad2", "2026-03-01", 10000.0, 3600.0)
    # Longer than "solo" but outside both the 365d window and "solo"'s own whole-km band (42km
    # floors to "42 km"; 60km floors to "60 km", a disjoint bucket under floor-km banding) --
    # keeps "solo" the 365d window's own longest without also making it the all-time longest, so
    # the title stays window-scoped rather than upgrading to "ever" (see the dedicated all-time
    # test below for that case).
    far_longer = _activity("far", "2024-01-01", 60000.0, 20000.0)
    insights = compute_activity_insights(
        "solo", [target, pad1, pad2, far_longer], dt.date(2026, 6, 15)
    )
    distance = next(i for i in insights if i.subject_key == "distance:run")
    assert distance.title == "Longest run in the last 12 months"
    pb = next(i for i in insights if i.kind == "pb" and i.subject_key == "pb:run:42 km")
    assert pb.title == "All-time best 42 km"


def test_avg_and_max_cadence_get_distinct_relabeled_titles() -> None:
    # "Highest cadence" alone would be ambiguous now that both an avg-cadence and a max-cadence
    # dimension exist -- each must relabel to a title that names which one it is.
    target = _activity(
        "solo", "2026-06-15", 5000.0, 1500.0, cadence=170.0, max_cadence=185.0
    )
    insights = compute_activity_insights("solo", [target], dt.date(2026, 6, 15))
    avg_cadence = next(i for i in insights if i.subject_key == "cadence:run")
    max_cadence = next(i for i in insights if i.subject_key == "max_cadence:run")
    assert avg_cadence.title == "Highest average cadence ever"
    assert max_cadence.title == "Highest max cadence ever"


def test_genuine_all_time_extreme_is_labeled_ever_not_a_window() -> None:
    target = _activity("target", "2026-06-15", 42000.0, 15000.0)
    pad1 = _activity("pad1", "2026-01-01", 10000.0, 3600.0)
    pad2 = _activity("pad2", "2026-03-01", 10000.0, 3600.0)
    # Every activity in the pool is inside the 365d window, so being that window's own longest
    # run is also, honestly, being the longest ever recorded.
    insights = compute_activity_insights("target", [target, pad1, pad2], dt.date(2026, 6, 15))
    distance = next(i for i in insights if i.subject_key == "distance:run")
    assert distance.title == "Longest run ever"


def test_temperature_insight_needs_at_least_three_comparable_activities() -> None:
    # Real-data motivated: a run flagged both "hottest in 6 months" and "coldest in 12 months"
    # because almost nothing else in either window had weather data -- "hottest of 1 or 2" isn't
    # a real comparison, so it must not be shown at all. Scoped to temperature only -- see the
    # next test for why the other dimensions must NOT be held to the same bar.
    target = _activity("solo", "2026-06-15", 42000.0, 15000.0, temperature_max_c=20.0)
    pad1 = _activity("pad1", "2026-01-01", 10000.0, 3600.0, temperature_max_c=10.0)
    insights = compute_activity_insights("solo", [target, pad1], dt.date(2026, 6, 15))
    assert not any(i.subject_key == "temperature_high" for i in insights)


def test_non_temperature_effort_insight_is_not_pool_checked() -> None:
    # The trivial-extreme guard (_MIN_COMPARISON_POOL) applies only to temperature, per explicit
    # user direction -- distance, duration, pace, HR, elevation, and start-time-of-day are
    # recorded on essentially every activity, so requiring 3 comparisons there would just
    # suppress ordinary "longest run" claims for a normal-sized history.
    target = _activity("solo", "2026-06-15", 42000.0, 15000.0)
    pad1 = _activity("pad1", "2026-01-01", 10000.0, 3600.0)
    insights = compute_activity_insights("solo", [target, pad1], dt.date(2026, 6, 15))
    assert any(i.subject_key == "distance:run" for i in insights)


def test_insights_about_other_activities_are_excluded() -> None:
    this_one = _activity("this_one", "2026-06-15", 5000.0, 1800.0)
    someone_elses_pb = _activity("other_pb", "2026-06-10", 10000.0, 3000.0)
    insights = compute_activity_insights(
        "this_one", [this_one, someone_elses_pb], dt.date(2026, 6, 15)
    )
    assert all(i.activity_id in (None, "this_one") for i in insights)


def test_non_running_sport_still_gets_effort_insights_scoped_to_its_own_family() -> None:
    ride = _activity("ride1", "2026-06-15", 40000.0, 5400.0, sport_family="ride")
    pad1 = _activity("ride2", "2026-01-01", 10000.0, 1800.0, sport_family="ride")
    pad2 = _activity("ride3", "2026-03-01", 10000.0, 1800.0, sport_family="ride")
    insights = compute_activity_insights("ride1", [ride, pad1, pad2], dt.date(2026, 6, 15))
    assert any(i.subject_key == "distance:ride" and i.activity_id == "ride1" for i in insights)


def test_recent_best_that_is_not_the_all_time_best_gets_a_window_best_insight() -> None:
    # Real-data motivated: an ordinary 6:26/km run was the fastest ~6km effort in the last 30
    # days (only one other, slower, candidate in that window), but ranked ~20th within the last
    # 365 days alone -- a much faster run from over a year back still holds the all-time record.
    # compute_pb_insights correctly stays silent for a claim that weak; this is the separate,
    # honestly-labelled insight that should fire instead. All distances floor to the same "6 km"
    # band (6000-6999m).
    all_time_best = _activity("old_pb", "2024-03-15", 6050.0, 1390.0)  # ~4:35/km
    target = _activity("recent", "2026-08-20", 6020.0, 2320.0)  # ~6:26/km
    other_recent = _activity("other_recent", "2026-07-25", 6030.0, 2430.0)  # slightly slower
    # Faster than "recent" but outside the 30d window (60 days back) -- present from the 90d
    # window on, so "recent" is only ever the window's own best at the narrowest (30d) level,
    # matching the real scenario this insight is meant to catch.
    faster_but_older = _activity("faster_but_older", "2026-06-21", 6040.0, 2200.0)
    insights = compute_activity_insights(
        "recent",
        [all_time_best, target, other_recent, faster_but_older],
        dt.date(2026, 8, 20),
    )
    assert not any(i.kind == "pb" for i in insights)  # not close to the all-time record
    window_best = next(
        i for i in insights if i.kind == "window_best" and i.subject_key == "window_best:run:6 km"
    )
    assert window_best.activity_id == "recent"
    assert window_best.title == "Fastest 6 km in the last 30 days"


def test_window_best_is_not_shown_when_it_coincides_with_the_all_time_best() -> None:
    # Would otherwise be a redundant, weaker echo of the "pb" insight for the same activity.
    slower = _activity("slower", "2026-01-01", 5000.0, 1600.0)
    fastest = _activity("fastest", "2026-08-10", 5000.0, 1200.0)
    insights = compute_activity_insights("fastest", [slower, fastest], dt.date(2026, 8, 14))
    assert any(i.kind == "pb" for i in insights)
    assert not any(i.kind == "window_best" for i in insights)


def test_window_best_dedupes_to_the_widest_window_still_true() -> None:
    all_time_best = _activity("old_pb", "2024-01-01", 5000.0, 1000.0)
    target = _activity("recent", "2026-08-14", 5000.0, 1300.0)
    insights = compute_activity_insights(
        "recent", [all_time_best, target], dt.date(2026, 8, 14)
    )
    window_best = [i for i in insights if i.kind == "window_best"]
    # "recent" is the only non-all-time-best candidate across every window here, so only the
    # single widest true claim should survive, not five near-identical repeats.
    assert len(window_best) == 1
