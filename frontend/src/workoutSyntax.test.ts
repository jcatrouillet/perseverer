// Tests for workoutSyntax.ts -- the client-side preview twin of src/perseverer/workout_syntax.py
// (see that module's own docstring). The bulk of forward-parse coverage runs against the same
// JSON fixture table the Python suite uses (tests/fixtures/workout_syntax_cases.json), so both
// implementations are asserted against identical inputs/outputs rather than trusted to agree by
// inspection -- same precedent as gap.ts/gap.py.
import { describe, expect, it } from "vitest";

import {
  type ParsedStep,
  type RecordedStepLike,
  parseWorkoutSyntax,
  stepsToSourceText,
} from "./workoutSyntax";
// A real JSON import (Vite's own resolveJsonModule support), not a Node `fs` read -- this
// project has no @types/node dependency, and importing the file directly is what actually lets
// both languages' test suites drive off the exact same fixture data (see workout_syntax.py's
// own test file) without adding one just for this.
import cases from "../../tests/fixtures/workout_syntax_cases.json";

// camelCase ParsedStep -> the fixture's snake_case field names, so the same JSON drives both
// languages' tests without a second hand-maintained fixture file to drift out of sync.
function toSnakeCase(step: ParsedStep): Record<string, unknown> {
  return {
    step_index: step.stepIndex,
    duration_type: step.durationType,
    duration_time_s: step.durationTimeS,
    duration_distance_m: step.durationDistanceM,
    target_type: step.targetType,
    target_low: step.targetLow,
    target_high: step.targetHigh,
    target_hr_zone: step.targetHrZone,
    cadence_low: step.cadenceLow,
    cadence_high: step.cadenceHigh,
    intensity: step.intensity,
    repeat_from_step: step.repeatFromStep,
    repeat_count: step.repeatCount,
  };
}

function approxEqual(a: unknown, b: unknown): boolean {
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) < 1e-9;
  return a === b;
}

describe("parseWorkoutSyntax fixture cases (shared with workout_syntax.py)", () => {
  for (const testCase of cases) {
    it(testCase.name, () => {
      const result = parseWorkoutSyntax(testCase.text);
      const actualSteps = result.steps.map(toSnakeCase);
      expect(actualSteps).toHaveLength(testCase.steps.length);
      actualSteps.forEach((actual, i) => {
        const expected = testCase.steps[i];
        for (const [key, expectedValue] of Object.entries(expected)) {
          expect(approxEqual(actual[key], expectedValue)).toBe(true);
        }
      });
      const actualErrorLines = result.errors.map((e) => e.lineNo);
      expect(actualErrorLines).toEqual(testCase.error_line_nos);
    });
  }
});

describe("estimatedDurationS", () => {
  it("sums warmup + repeat block + cooldown", () => {
    const text = "Warmup 10m\n\n4x\n3m 5:00-5:10/km Pace\n2m Z2 HR\n\nCooldown 5m";
    const result = parseWorkoutSyntax(text);
    expect(result.estimatedDurationS).toBeCloseTo(2100, 6);
  });

  it("estimates a distance step from its pace target", () => {
    const result = parseWorkoutSyntax("2km 5:00/km Pace");
    expect(result.estimatedDurationS).toBeCloseTo(600, 3);
  });

  it("falls back to the default assumed speed with no target", () => {
    const result = parseWorkoutSyntax("3000mtr");
    expect(result.estimatedDurationS).toBeCloseTo(3000 / 3.0, 6);
  });
});

describe("stepsToSourceText", () => {
  it("round-trips a simple warmup/main/cooldown structure", () => {
    const recorded: RecordedStepLike[] = [
      {
        stepIndex: 0,
        durationType: "time",
        durationTimeS: 600,
        durationDistanceM: null,
        targetType: null,
        targetLowMps: null,
        targetHighMps: null,
        intensity: "warmup",
        repeatFromStep: null,
        repeatCount: null,
      },
      {
        stepIndex: 1,
        durationType: "time",
        durationTimeS: 180,
        durationDistanceM: null,
        targetType: "speed",
        targetLowMps: 3.125,
        targetHighMps: 3.3333333333333335,
        intensity: "active",
        repeatFromStep: null,
        repeatCount: null,
      },
      {
        stepIndex: 2,
        durationType: "time",
        durationTimeS: 300,
        durationDistanceM: null,
        targetType: null,
        targetLowMps: null,
        targetHighMps: null,
        intensity: "cooldown",
        repeatFromStep: null,
        repeatCount: null,
      },
    ];
    const text = stepsToSourceText(recorded);
    const reparsed = parseWorkoutSyntax(text);
    expect(reparsed.errors).toEqual([]);
    expect(reparsed.steps).toHaveLength(3);
    expect(reparsed.steps[0].intensity).toBe("warmup");
    expect(reparsed.steps[1].targetType).toBe("pace");
    expect(reparsed.steps[1].targetLow).toBeCloseTo(3.125, 6);
    expect(reparsed.steps[2].intensity).toBe("cooldown");
  });

  it("round-trips a repeat block", () => {
    const recorded: RecordedStepLike[] = [
      {
        stepIndex: 0,
        durationType: "time",
        durationTimeS: 600,
        durationDistanceM: null,
        targetType: null,
        targetLowMps: null,
        targetHighMps: null,
        intensity: "warmup",
        repeatFromStep: null,
        repeatCount: null,
      },
      {
        stepIndex: 1,
        durationType: "time",
        durationTimeS: 180,
        durationDistanceM: null,
        targetType: "speed",
        targetLowMps: 3.125,
        targetHighMps: 3.3333333333333335,
        intensity: null,
        repeatFromStep: null,
        repeatCount: null,
      },
      {
        stepIndex: 2,
        durationType: "time",
        durationTimeS: 120,
        durationDistanceM: null,
        targetType: null,
        targetLowMps: null,
        targetHighMps: null,
        intensity: "recovery",
        repeatFromStep: null,
        repeatCount: null,
      },
      {
        stepIndex: 3,
        durationType: "repeat_until_steps_cmplt",
        durationTimeS: null,
        durationDistanceM: null,
        targetType: null,
        targetLowMps: null,
        targetHighMps: null,
        intensity: null,
        repeatFromStep: 1,
        repeatCount: 4,
      },
      {
        stepIndex: 4,
        durationType: "time",
        durationTimeS: 300,
        durationDistanceM: null,
        targetType: null,
        targetLowMps: null,
        targetHighMps: null,
        intensity: "cooldown",
        repeatFromStep: null,
        repeatCount: null,
      },
    ];
    const text = stepsToSourceText(recorded);
    const reparsed = parseWorkoutSyntax(text);
    expect(reparsed.errors).toEqual([]);
    const repeatSteps = reparsed.steps.filter((s) => s.durationType === "repeat_until_steps_cmplt");
    expect(repeatSteps).toHaveLength(1);
    expect(repeatSteps[0].repeatCount).toBe(4);
  });

  it("skips a step with no recognizable duration", () => {
    const recorded: RecordedStepLike[] = [
      {
        stepIndex: 0,
        durationType: "reps",
        durationTimeS: null,
        durationDistanceM: null,
        targetType: null,
        targetLowMps: null,
        targetHighMps: null,
        intensity: null,
        repeatFromStep: null,
        repeatCount: null,
      },
    ];
    expect(stepsToSourceText(recorded)).toBe("");
  });
});
