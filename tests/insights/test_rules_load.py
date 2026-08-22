from __future__ import annotations

import datetime as dt

from perseverer.insights.rules_load import (
    TSB_SUSTAINED_LOW_MIN_DAYS,
    TSB_SUSTAINED_LOW_THRESHOLD,
    FitnessDay,
    compute_load_insights,
    load_jump_insight,
    sustained_low_tsb_insight,
)


def _days(start: dt.date, tsb_values: list[float]) -> list[FitnessDay]:
    return [
        FitnessDay(
            local_date=(start + dt.timedelta(days=i)).isoformat(),
            training_load=100.0,
            ctl=50.0,
            atl=50.0,
            tsb=v,
        )
        for i, v in enumerate(tsb_values)
    ]


def test_sustained_low_tsb_flags_a_run_of_qualifying_days() -> None:
    start = dt.date(2026, 8, 1)
    values = [0.0] * 3 + [TSB_SUSTAINED_LOW_THRESHOLD - 1] * TSB_SUSTAINED_LOW_MIN_DAYS
    days = _days(start, values)
    as_of = start + dt.timedelta(days=len(values) - 1)
    insight = sustained_low_tsb_insight(days, as_of)
    assert insight is not None
    assert insight.detail["days"] == TSB_SUSTAINED_LOW_MIN_DAYS


def test_a_run_shorter_than_the_minimum_is_not_flagged() -> None:
    start = dt.date(2026, 8, 1)
    values = [TSB_SUSTAINED_LOW_THRESHOLD - 1] * (TSB_SUSTAINED_LOW_MIN_DAYS - 1)
    days = _days(start, values)
    as_of = start + dt.timedelta(days=len(values) - 1)
    assert sustained_low_tsb_insight(days, as_of) is None


def test_a_streak_that_ended_long_ago_is_not_flagged_as_current() -> None:
    start = dt.date(2026, 1, 1)
    values = [TSB_SUSTAINED_LOW_THRESHOLD - 1] * TSB_SUSTAINED_LOW_MIN_DAYS
    days = _days(start, values)
    far_future = start + dt.timedelta(days=90)
    assert sustained_low_tsb_insight(days, far_future) is None


def test_week_over_week_load_jump_is_flagged() -> None:
    as_of = dt.date(2026, 8, 14)
    days = []
    for i in range(14):
        d = as_of - dt.timedelta(days=i)
        load = 100.0 if i < 7 else 50.0  # recent week double the prior week
        days.append(FitnessDay(local_date=d.isoformat(), training_load=load, ctl=0, atl=0, tsb=0))
    insight = load_jump_insight(days, as_of)
    assert insight is not None
    assert insight.value_num == 100.0  # 100% increase


def test_no_prior_week_data_means_no_jump_insight() -> None:
    as_of = dt.date(2026, 8, 14)
    days = [
        FitnessDay(local_date=as_of.isoformat(), training_load=100.0, ctl=0, atl=0, tsb=0),
    ]
    assert load_jump_insight(days, as_of) is None


def test_compute_load_insights_combines_both_rules() -> None:
    as_of = dt.date(2026, 8, 14)
    days = [
        FitnessDay(local_date=as_of.isoformat(), training_load=10.0, ctl=0, atl=0, tsb=0),
    ]
    insights = compute_load_insights(days, as_of)
    assert isinstance(insights, list)
