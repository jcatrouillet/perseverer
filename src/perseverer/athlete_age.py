"""One tiny shared helper: an athlete's age as of a given date, from their own configured
`athlete.birthdate`. Used by performance_rollup.py (the max-HR formula fallback) and
api/routers/health.py (the BMR formula fallback) -- factored out here rather than duplicated in
both, since both need the exact same "age as of THIS day" math, not just "age today".
"""

from __future__ import annotations

from datetime import date


def age_years_as_of(birthdate: date, as_of: date) -> float:
    """A fractional age in years -- fractional (not just whole years) because both callers feed
    this straight into a continuous formula (Tanaka's max-HR, Mifflin-St Jeor's BMR), where a
    whole-number age would introduce a small but pointless step discontinuity on each birthday.
    365.25 (not 365) accounts for leap years over a multi-decade span.
    """
    return (as_of - birthdate).days / 365.25
