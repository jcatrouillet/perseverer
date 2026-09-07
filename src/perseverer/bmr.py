"""Basal metabolic rate (BMR) -- a formula-based FALLBACK for `api/routers/health.py`'s
`bmr_kcal` logical metric, used only on a day that has a resolved body weight but no vendor
(Eufy scale) BMR reading, and only when the athlete has a fully configured profile (birthdate,
height, sex). Never overrides a real Eufy `eufy.scale.bmr` reading -- same "empirical/device
data wins, formula only fills the gap" posture as performance_rollup.py's own max-HR fallback
(see athlete_age.py).

Mifflin-St Jeor (Mifflin et al., "A new predictive equation for resting energy expenditure in
healthy individuals", 1990) -- chosen over the older Harris-Benedict equation, which it has been
shown to more accurately predict resting energy expenditure. Formula:

    male:   BMR = 10*weight_kg + 6.25*height_cm - 5*age_years + 5
    female: BMR = 10*weight_kg + 6.25*height_cm - 5*age_years - 161

The formula itself only defines a binary male/female constant -- a real, disclosed limitation of
this specific equation, not something this module papers over or extends.
"""

from __future__ import annotations

_MALE_CONSTANT = 5.0
_FEMALE_CONSTANT = -161.0


def compute_bmr_kcal(weight_kg: float, height_cm: float, age_years: float, sex: str) -> float:
    if sex not in ("male", "female"):
        raise ValueError(f"sex must be 'male' or 'female', got {sex!r}")
    constant = _MALE_CONSTANT if sex == "male" else _FEMALE_CONSTANT
    return 10 * weight_kg + 6.25 * height_cm - 5 * age_years + constant
