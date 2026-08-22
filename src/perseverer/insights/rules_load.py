"""Load & recovery flags: rules over the existing CTL/ATL/TSB fitness rollup
(`fitness_daily_rollup`, see fitness.py). Thresholds are deliberately-chosen defaults, not
derived from any sports-science reference this project can verify against real data the way
every other threshold in this codebase is -- called out explicitly here (and in ADR 0012) for
the user to confirm or adjust, per the project brief's own instruction to ask rather than guess
on threshold-like decisions.

Pure functions over a plain `(local_date, training_load, ctl, atl, tsb)` daily series, sorted
ascending by date -- no DB access, matching every other rule module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from perseverer.insights.types import Insight

# TSB (Training Stress Balance) sustained below this value signals accumulated fatigue --
# -20 is Coggan's own commonly-cited "high risk of overreaching" line, but this project has no
# way to verify that line against this athlete's real outcomes, so treat it as an adjustable
# default, not a validated threshold.
TSB_SUSTAINED_LOW_THRESHOLD = -20.0
TSB_SUSTAINED_LOW_MIN_DAYS = 5
# How far back to look for a sustained-low streak that's still "current" (ending at or after
# this many days before as_of) -- a streak that ended a month ago isn't an actionable flag today.
TSB_LOOKBACK_DAYS = 14

# A week-over-week training-load jump larger than this fraction flags a ramp-rate concern.
LOAD_JUMP_PCT_THRESHOLD = 0.30


@dataclass(frozen=True)
class FitnessDay:
    local_date: str
    training_load: float
    ctl: float
    atl: float
    tsb: float


def sustained_low_tsb_insight(days: list[FitnessDay], as_of: date) -> Insight | None:
    """Flags the most recent run of >= TSB_SUSTAINED_LOW_MIN_DAYS consecutive days with
    tsb <= TSB_SUSTAINED_LOW_THRESHOLD, if that run ends within TSB_LOOKBACK_DAYS of as_of."""
    if not days:
        return None
    run_start_idx: int | None = None
    best: tuple[int, int] | None = None  # (start_idx, end_idx) of the best qualifying run
    for i, d in enumerate(days):
        if d.tsb <= TSB_SUSTAINED_LOW_THRESHOLD:
            if run_start_idx is None:
                run_start_idx = i
        else:
            if run_start_idx is not None and i - run_start_idx >= TSB_SUSTAINED_LOW_MIN_DAYS:
                best = (run_start_idx, i - 1)
            run_start_idx = None
    if run_start_idx is not None and len(days) - run_start_idx >= TSB_SUSTAINED_LOW_MIN_DAYS:
        best = (run_start_idx, len(days) - 1)
    if best is None:
        return None

    start_idx, end_idx = best
    end_date = date.fromisoformat(days[end_idx].local_date)
    if (as_of - end_date).days > TSB_LOOKBACK_DAYS:
        return None

    run_length = end_idx - start_idx + 1
    min_tsb = min(d.tsb for d in days[start_idx : end_idx + 1])
    return Insight(
        kind="load",
        window="current",
        subject_key="load:sustained_low_tsb",
        title=(
            f"Training stress balance below {TSB_SUSTAINED_LOW_THRESHOLD:.0f} "
            f"for {run_length} days"
        ),
        detail={
            "start": days[start_idx].local_date,
            "end": days[end_idx].local_date,
            "days": run_length,
            "min_tsb": round(min_tsb, 1),
        },
        value_num=round(min_tsb, 1),
        local_date=days[end_idx].local_date,
    )


def load_jump_insight(days: list[FitnessDay], as_of: date) -> Insight | None:
    """Compares the 7 days ending at as_of against the preceding 7 days' total training_load."""
    by_date = {d.local_date: d.training_load for d in days}
    recent_window = [as_of - timedelta(days=i) for i in range(7)]
    prior_window = [as_of - timedelta(days=i) for i in range(7, 14)]
    recent_total = sum(by_date.get(d.isoformat(), 0.0) for d in recent_window)
    prior_total = sum(by_date.get(d.isoformat(), 0.0) for d in prior_window)
    if prior_total <= 0:
        return None
    pct_change = (recent_total - prior_total) / prior_total
    if pct_change < LOAD_JUMP_PCT_THRESHOLD:
        return None
    return Insight(
        kind="load",
        window="current",
        subject_key="load:week_over_week_jump",
        title=f"Weekly training load up {pct_change * 100:.0f}% vs the prior week",
        detail={
            "recent_total": round(recent_total, 1),
            "prior_total": round(prior_total, 1),
            "pct_change": round(pct_change * 100, 1),
        },
        value_num=round(pct_change * 100, 1),
        local_date=as_of.isoformat(),
    )


def compute_load_insights(days: list[FitnessDay], as_of: date) -> list[Insight]:
    insights: list[Insight] = []
    sustained = sustained_low_tsb_insight(days, as_of)
    if sustained is not None:
        insights.append(sustained)
    jump = load_jump_insight(days, as_of)
    if jump is not None:
        insights.append(jump)
    return insights
