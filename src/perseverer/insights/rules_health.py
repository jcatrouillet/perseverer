"""Health/wellness anomalies: rolling-baseline deviation for resting heart rate and sleep
score, read from data already ingested and rolled up (`health_metric_daily_rollup`,
`sleep_session.sleep_score`) -- no new health ingestion. Pure functions over a plain
`(local_date, value)` series sorted ascending by date, no DB access.

Thresholds (10% above baseline for resting HR, 15% below baseline for sleep score) are
adjustable defaults, not validated against this athlete's real outcomes -- same caveat as
rules_load.py's thresholds, called out in docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from datetime import date, timedelta

from perseverer.insights.types import Insight

RESTING_HR_ELEVATED_PCT = 0.10
SLEEP_SCORE_DROP_PCT = 0.15
BASELINE_LOOKBACK_DAYS = 30
BASELINE_MIN_OBSERVATIONS = 7


def _trailing_baseline(
    series: list[tuple[str, float]], as_of: date, *, exclude_days: int = 1
) -> float | None:
    cutoff_end = (as_of - timedelta(days=exclude_days)).isoformat()
    cutoff_start = (as_of - timedelta(days=BASELINE_LOOKBACK_DAYS)).isoformat()
    window = [v for d, v in series if cutoff_start <= d <= cutoff_end]
    if len(window) < BASELINE_MIN_OBSERVATIONS:
        return None
    return sum(window) / len(window)


def resting_hr_anomaly(series: list[tuple[str, float]], as_of: date) -> Insight | None:
    """An insight when today's resting HR sits well above the trailing baseline; None without enough
    history or without a reading today.
    """
    if not series or series[-1][0] != as_of.isoformat():
        return None
    latest_value = series[-1][1]
    baseline = _trailing_baseline(series, as_of)
    if baseline is None or baseline <= 0:
        return None
    pct = (latest_value - baseline) / baseline
    if pct < RESTING_HR_ELEVATED_PCT:
        return None
    return Insight(
        kind="health",
        window="current",
        subject_key="health:resting_hr_elevated",
        title="Resting heart rate elevated vs. your recent baseline",
        detail={
            "latest": latest_value,
            "baseline": round(baseline, 1),
            "pct_above_baseline": round(pct * 100, 1),
        },
        value_num=latest_value,
        metric_key="resting_heart_rate",
        local_date=as_of.isoformat(),
    )


def sleep_score_anomaly(series: list[tuple[str, float]], as_of: date) -> Insight | None:
    """An insight when today's sleep score drops well below the trailing baseline; None without
    enough history or without a score today.
    """
    if not series or series[-1][0] != as_of.isoformat():
        return None
    latest_value = series[-1][1]
    baseline = _trailing_baseline(series, as_of)
    if baseline is None or baseline <= 0:
        return None
    pct = (baseline - latest_value) / baseline
    if pct < SLEEP_SCORE_DROP_PCT:
        return None
    return Insight(
        kind="health",
        window="current",
        subject_key="health:sleep_score_drop",
        title="Sleep score below your recent baseline",
        detail={
            "latest": latest_value,
            "baseline": round(baseline, 1),
            "pct_below_baseline": round(pct * 100, 1),
        },
        value_num=latest_value,
        metric_key="sleep_score",
        local_date=as_of.isoformat(),
    )


def compute_health_insights(
    resting_hr_series: list[tuple[str, float]],
    sleep_score_series: list[tuple[str, float]],
    as_of: date,
) -> list[Insight]:
    """Every health anomaly insight that applies as of `as_of`."""
    insights: list[Insight] = []
    hr = resting_hr_anomaly(resting_hr_series, as_of)
    if hr is not None:
        insights.append(hr)
    sleep = sleep_score_anomaly(sleep_score_series, as_of)
    if sleep is not None:
        insights.append(sleep)
    return insights
