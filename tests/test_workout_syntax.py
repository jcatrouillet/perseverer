"""Tests for workout_syntax.py -- the authoritative scheduled-workout text parser (see its own
module docstring). The bulk of the forward-parse coverage lives in a JSON fixture table
(`tests/fixtures/workout_syntax_cases.json`) shared with the TS twin's own test suite
(`frontend/src/workoutSyntax.test.ts`), so both implementations are asserted against identical
inputs/outputs rather than trusted to agree by inspection -- same precedent as gap.py/gap.ts.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from perseverer.workout_syntax import (
    RecordedStepLike,
    parse_workout_syntax,
    steps_to_source_text,
)

_FIXTURES_PATH = Path(__file__).parent / "fixtures" / "workout_syntax_cases.json"
_CASES: list[dict[str, Any]] = json.loads(_FIXTURES_PATH.read_text())


def _approx_eq(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-9
    return a == b


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_fixture_case(case: dict[str, Any]) -> None:
    result = parse_workout_syntax(case["text"])
    actual_steps = [asdict(s) for s in result.steps]

    assert len(actual_steps) == len(case["steps"]), (
        f"{case['name']}: expected {len(case['steps'])} steps, got {len(actual_steps)}: "
        f"{actual_steps}"
    )
    for i, (actual, expected) in enumerate(zip(actual_steps, case["steps"], strict=True)):
        for key, expected_value in expected.items():
            actual_value = actual.get(key)
            assert _approx_eq(actual_value, expected_value), (
                f"{case['name']} step {i} field {key!r}: "
                f"expected {expected_value!r}, got {actual_value!r}"
            )

    actual_error_lines = [e.line_no for e in result.errors]
    assert actual_error_lines == case["error_line_nos"], (
        f"{case['name']}: expected error lines {case['error_line_nos']}, got {actual_error_lines}"
    )


def test_estimated_duration_sums_warmup_repeat_and_cooldown() -> None:
    # 10m warmup + 4x(3m + 2m) + 5m cooldown = 600 + 4*300 + 300 = 2100s.
    text = "Warmup 10m\n\n4x\n3m 5:00-5:10/km Pace\n2m Z2 HR\n\nCooldown 5m"
    result = parse_workout_syntax(text)
    assert result.estimated_duration_s == pytest.approx(2100.0)


def test_estimated_duration_for_distance_step_uses_pace_target_midpoint() -> None:
    # 2km at an exact 5:00/km pace target (200 mps... no: speed = 1000/300 m/s) should take
    # exactly 600s -- distance / speed.
    result = parse_workout_syntax("2km 5:00/km Pace")
    assert result.estimated_duration_s == pytest.approx(600.0, rel=1e-6)


def test_estimated_duration_for_distance_step_with_no_target_uses_default_assumed_speed() -> None:
    result = parse_workout_syntax("3000mtr")
    assert result.estimated_duration_s == pytest.approx(3000.0 / 3.0)


class TestStepsToSourceText:
    """The reverse direction: a known activity_workout_step-shaped row set round-trips to text
    and back to the same structured steps -- the conversion the calendar's "Copy" action needs
    (see module docstring)."""

    def test_simple_warmup_main_cooldown_round_trips(self) -> None:
        recorded = [
            RecordedStepLike(0, "time", 600, None, None, None, None, "warmup", None, None),
            RecordedStepLike(
                1, "time", 180, None, "speed", 3.125, 3.3333333333333335, "active", None, None
            ),
            RecordedStepLike(2, "time", 300, None, None, None, None, "cooldown", None, None),
        ]
        text = steps_to_source_text(recorded)
        reparsed = parse_workout_syntax(text)
        assert reparsed.errors == []
        assert len(reparsed.steps) == 3
        assert reparsed.steps[0].duration_type == "time"
        assert reparsed.steps[0].duration_time_s == 600
        assert reparsed.steps[0].intensity == "warmup"
        assert reparsed.steps[1].target_type == "pace"
        assert reparsed.steps[1].target_low == pytest.approx(3.125)
        assert reparsed.steps[1].target_high == pytest.approx(3.3333333333333335)
        assert reparsed.steps[2].intensity == "cooldown"

    def test_lap_button_step_round_trips_with_its_estimate(self) -> None:
        recorded = [
            RecordedStepLike(0, "lap_button", None, 5000, None, None, None, None, None, None),
        ]
        text = steps_to_source_text(recorded)
        assert text.strip() == "lap 5km"
        reparsed = parse_workout_syntax(text)
        assert reparsed.errors == []
        assert reparsed.steps[0].duration_type == "lap_button"
        assert reparsed.steps[0].duration_distance_m == pytest.approx(5000)

    def test_bare_lap_button_step_round_trips(self) -> None:
        recorded = [
            RecordedStepLike(0, "lap_button", None, None, None, None, None, "warmup", None, None),
        ]
        text = steps_to_source_text(recorded)
        assert text.strip() == "Warmup lap"
        reparsed = parse_workout_syntax(text)
        assert reparsed.errors == []
        assert reparsed.steps[0].duration_type == "lap_button"
        assert reparsed.steps[0].intensity == "warmup"

    def test_repeat_block_round_trips(self) -> None:
        recorded = [
            RecordedStepLike(0, "time", 600, None, None, None, None, "warmup", None, None),
            RecordedStepLike(
                1, "time", 180, None, "speed", 3.125, 3.3333333333333335, None, None, None
            ),
            RecordedStepLike(2, "time", 120, None, None, None, None, "recovery", None, None),
            RecordedStepLike(
                3, "repeat_until_steps_cmplt", None, None, None, None, None, None, 1, 4
            ),
            RecordedStepLike(4, "time", 300, None, None, None, None, "cooldown", None, None),
        ]
        text = steps_to_source_text(recorded)
        reparsed = parse_workout_syntax(text)
        assert reparsed.errors == []
        repeat_steps = [s for s in reparsed.steps if s.duration_type == "repeat_until_steps_cmplt"]
        assert len(repeat_steps) == 1
        assert repeat_steps[0].repeat_count == 4

    def test_step_with_no_recognizable_duration_is_skipped(self) -> None:
        recorded = [RecordedStepLike(0, "reps", None, None, None, None, None, None, None, None)]
        assert steps_to_source_text(recorded) == ""
