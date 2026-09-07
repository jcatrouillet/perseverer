"""Tests for bmr.compute_bmr_kcal -- the Mifflin-St Jeor formula used as api/routers/health.py's
BMR fallback. Reference values hand-computed from the published equation, not derived from the
implementation itself."""

import pytest

from perseverer.bmr import compute_bmr_kcal


def test_male_reference_value() -> None:
    # 10*70 + 6.25*175 - 5*30 + 5 = 700 + 1093.75 - 150 + 5 = 1648.75
    bmr = compute_bmr_kcal(weight_kg=70, height_cm=175, age_years=30, sex="male")
    assert bmr == pytest.approx(1648.75)


def test_female_reference_value() -> None:
    # 10*70 + 6.25*175 - 5*30 - 161 = 700 + 1093.75 - 150 - 161 = 1482.75
    bmr = compute_bmr_kcal(weight_kg=70, height_cm=175, age_years=30, sex="female")
    assert bmr == pytest.approx(1482.75)


def test_male_and_female_differ_by_exactly_the_constant() -> None:
    male = compute_bmr_kcal(weight_kg=60, height_cm=160, age_years=25, sex="male")
    female = compute_bmr_kcal(weight_kg=60, height_cm=160, age_years=25, sex="female")
    assert male - female == pytest.approx(166.0)


def test_invalid_sex_raises() -> None:
    with pytest.raises(ValueError, match="sex must be"):
        compute_bmr_kcal(weight_kg=70, height_cm=175, age_years=30, sex="other")
