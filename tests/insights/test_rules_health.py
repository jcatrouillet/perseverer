from __future__ import annotations

import datetime as dt

from perseverer.insights.rules_health import (
    RESTING_HR_ELEVATED_PCT,
    SLEEP_SCORE_DROP_PCT,
    compute_health_insights,
    resting_hr_anomaly,
    sleep_score_anomaly,
)


def _baseline_series(
    as_of: dt.date, baseline_value: float, days: int = 30
) -> list[tuple[str, float]]:
    return [
        ((as_of - dt.timedelta(days=i)).isoformat(), baseline_value) for i in range(1, days + 1)
    ]


def test_resting_hr_elevated_above_baseline_is_flagged() -> None:
    as_of = dt.date(2026, 8, 14)
    series = _baseline_series(as_of, 50.0)
    series.append((as_of.isoformat(), 50.0 * (1 + RESTING_HR_ELEVATED_PCT + 0.05)))
    series.sort()
    insight = resting_hr_anomaly(series, as_of)
    assert insight is not None
    assert insight.metric_key == "resting_heart_rate"


def test_resting_hr_within_normal_range_is_not_flagged() -> None:
    as_of = dt.date(2026, 8, 14)
    series = _baseline_series(as_of, 50.0)
    series.append((as_of.isoformat(), 51.0))
    series.sort()
    assert resting_hr_anomaly(series, as_of) is None


def test_no_reading_today_means_no_anomaly_insight() -> None:
    as_of = dt.date(2026, 8, 14)
    series = _baseline_series(as_of, 50.0)
    assert resting_hr_anomaly(series, as_of) is None


def test_insufficient_baseline_history_means_no_insight() -> None:
    as_of = dt.date(2026, 8, 14)
    series = [(as_of.isoformat(), 80.0)]  # no history at all
    assert resting_hr_anomaly(series, as_of) is None


def test_sleep_score_drop_below_baseline_is_flagged() -> None:
    as_of = dt.date(2026, 8, 14)
    series = _baseline_series(as_of, 80.0)
    series.append((as_of.isoformat(), 80.0 * (1 - SLEEP_SCORE_DROP_PCT - 0.05)))
    series.sort()
    insight = sleep_score_anomaly(series, as_of)
    assert insight is not None
    assert insight.metric_key == "sleep_score"


def test_compute_health_insights_combines_both_series() -> None:
    as_of = dt.date(2026, 8, 14)
    hr_series = _baseline_series(as_of, 50.0)
    sleep_series = _baseline_series(as_of, 80.0)
    insights = compute_health_insights(hr_series, sleep_series, as_of)
    assert insights == []  # neither series has a reading for as_of itself
