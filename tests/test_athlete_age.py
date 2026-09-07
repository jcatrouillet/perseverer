"""Tests for athlete_age.age_years_as_of -- the shared "age as of a given day" helper used by
both the max-HR (Tanaka) and BMR (Mifflin-St Jeor) formula fallbacks."""

import datetime as dt

from perseverer.athlete_age import age_years_as_of


def test_exact_birthday_is_a_whole_number_of_years() -> None:
    birthdate = dt.date(1990, 6, 15)
    as_of = dt.date(2025, 6, 15)
    age = age_years_as_of(birthdate, as_of)
    # Not exactly 35 due to leap years within the span, but very close.
    assert abs(age - 35) < 0.01


def test_day_before_birthday_is_just_under_a_whole_year() -> None:
    birthdate = dt.date(1990, 6, 15)
    as_of = dt.date(2025, 6, 14)
    age = age_years_as_of(birthdate, as_of)
    assert 34.99 < age < 35.0


def test_same_day_is_zero() -> None:
    birthdate = dt.date(2000, 1, 1)
    assert age_years_as_of(birthdate, birthdate) == 0.0
