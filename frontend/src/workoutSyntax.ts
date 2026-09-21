// The scheduled-workout text syntax -- the TS twin of src/perseverer/workout_syntax.py (see its
// own module docstring for the full grammar and the intervals.icu inspiration/scope). This copy
// exists for an *instant client-side preview* as the athlete types in the schedule form
// (planned-workout UI, MonthView.tsx); the Python module runs the *authoritative* parse
// server-side on save. Kept in sync via a shared fixture table exercised by both languages' test
// suites (workoutSyntax.fixtures.ts / tests/workout_syntax_fixtures.py) rather than trusted to
// agree by inspection -- same precedent as gap.ts/gap.py.
//
// Grammar for one non-repeat-marker line (space-separated tokens, keywords case-insensitive):
//   [intensity] duration [target] [cadence]
// A standalone "<N>x" line starts a repeat block: every following non-blank line up to the next
// blank line (or end of text) is one of its children. The boundary is the blank line, NOT
// indentation -- a child's own leading whitespace is stripped and otherwise ignored, so a step
// meant to come after the repeat needs its own blank line before it, even if its children were
// typed indented. See workout_syntax.py's docstring for the full grammar plus a worked example
// of exactly this mistake -- this file mirrors that parser's behavior token-for-token.
import type { PlannedWorkoutStepOut } from "./api/types";

export const INTENSITY_WORDS = new Set(["warmup", "cooldown", "recovery", "rest", "active"]);

const REPEAT_MARKER_RE = /^(\d+)\s*[xX]$/;

const DIST_KM_RE = /^(\d+(?:\.\d+)?)km$/i;
const DIST_MTR_RE = /^(\d+(?:\.\d+)?)mtr$/i;
const DIST_MI_RE = /^(\d+(?:\.\d+)?)mi$/i;
const TIME_MS_RE = /^(\d+)m(\d+)s$/i;
const TIME_M_RE = /^(\d+)m$/i;
const TIME_S_RE = /^(\d+)s$/i;

const PACE_RANGE_RE = /^(\d+):(\d{2})-(\d+):(\d{2})\/km$/;
const PACE_SINGLE_RE = /^(\d+):(\d{2})\/km$/;
const HR_ZONE_RE = /^Z(\d)$/i;
const HR_RANGE_RE = /^(\d+)-(\d+)$/;
const HR_SINGLE_RE = /^(\d+)$/;
const CADENCE_RE = /^(\d+)(?:-(\d+))?spm$/i;

/** The duration keyword that ends a step on the watch's lap button instead of time/distance --
 * mirrors workout_syntax.LAP_BUTTON_WORD. */
const LAP_BUTTON_WORD = "lap";

const MILE_IN_METERS = 1609.34;
const DEFAULT_ASSUMED_SPEED_MPS = 3.0;

export interface ParsedStep {
  stepIndex: number;
  /** A "lap_button" step may still carry durationTimeS/durationDistanceM -- an estimate only,
   * never an end condition. See workout_syntax.py's docstring. */
  durationType: "time" | "distance" | "lap_button" | "repeat_until_steps_cmplt" | null;
  durationTimeS: number | null;
  durationDistanceM: number | null;
  targetType: "pace" | "heart_rate" | null;
  targetLow: number | null;
  targetHigh: number | null;
  targetHrZone: number | null;
  cadenceLow: number | null;
  cadenceHigh: number | null;
  intensity: string | null;
  repeatFromStep: number | null;
  repeatCount: number | null;
  comment: string | null;
}

export interface ParseError {
  lineNo: number;
  message: string;
}

export interface ParsedWorkout {
  steps: ParsedStep[];
  errors: ParseError[];
  estimatedDurationS: number;
}

function emptyStep(stepIndex: number): ParsedStep {
  return {
    stepIndex,
    durationType: null,
    durationTimeS: null,
    durationDistanceM: null,
    targetType: null,
    targetLow: null,
    targetHigh: null,
    targetHrZone: null,
    cadenceLow: null,
    cadenceHigh: null,
    intensity: null,
    repeatFromStep: null,
    repeatCount: null,
    comment: null,
  };
}

/** Does NOT handle the `lap` keyword: a lap-button step's optional trailing estimate is an
 * ordinary duration token, so parseStepLine calls this function for that estimate after already
 * consuming "lap" itself -- mirrors workout_syntax.py::_parse_duration's own docstring note. */
function parseDuration(
  token: string,
): { type: "time" | "distance"; timeS: number | null; distanceM: number | null } | null {
  let m = DIST_KM_RE.exec(token);
  if (m) return { type: "distance", timeS: null, distanceM: parseFloat(m[1]) * 1000 };
  m = DIST_MTR_RE.exec(token);
  if (m) return { type: "distance", timeS: null, distanceM: parseFloat(m[1]) };
  m = DIST_MI_RE.exec(token);
  if (m) return { type: "distance", timeS: null, distanceM: parseFloat(m[1]) * MILE_IN_METERS };
  m = TIME_MS_RE.exec(token);
  if (m) return { type: "time", timeS: parseInt(m[1], 10) * 60 + parseInt(m[2], 10), distanceM: null };
  m = TIME_M_RE.exec(token);
  if (m) return { type: "time", timeS: parseInt(m[1], 10) * 60, distanceM: null };
  m = TIME_S_RE.exec(token);
  if (m) return { type: "time", timeS: parseInt(m[1], 10), distanceM: null };
  return null;
}

function paceToSpeedMps(minutes: string, seconds: string): number {
  const totalS = parseInt(minutes, 10) * 60 + parseInt(seconds, 10);
  return totalS > 0 ? 1000 / totalS : 0;
}

/** Splits a trailing "# comment text" off a line -- only the FIRST "#" starts the comment;
 * further "#" characters are just part of the comment text. No "#" at all -> [line, null]. */
function splitComment(line: string): [string, string | null] {
  const hashIndex = line.indexOf("#");
  if (hashIndex === -1) return [line, null];
  const stepPart = line.slice(0, hashIndex).trimEnd();
  const comment = line.slice(hashIndex + 1).trim();
  return [stepPart, comment || null];
}

function parseStepLine(line: string, lineNo: number, errors: ParseError[]): ParsedStep | null {
  const tokens = line.split(/\s+/).filter(Boolean);
  let i = 0;
  let intensity: string | null = null;
  if (tokens.length > 0 && INTENSITY_WORDS.has(tokens[0].toLowerCase())) {
    intensity = tokens[0].toLowerCase();
    i = 1;
  }

  if (i >= tokens.length) {
    errors.push({ lineNo, message: `missing duration: ${JSON.stringify(line)}` });
    return null;
  }
  const step = emptyStep(-1);
  step.intensity = intensity;

  if (tokens[i].toLowerCase() === LAP_BUTTON_WORD) {
    // "lap" ends the step on the watch's lap button. An ordinary duration token may follow as an
    // estimate for the calendar's planned-duration/load figures only -- deliberately NOT an end
    // condition, so a step that overruns its estimate still waits for the button rather than
    // advancing and swapping targets mid-effort.
    step.durationType = "lap_button";
    i += 1;
    if (i < tokens.length) {
      const estimate = parseDuration(tokens[i]);
      if (estimate !== null) {
        step.durationTimeS = estimate.timeS;
        step.durationDistanceM = estimate.distanceM;
        i += 1;
      }
    }
  } else {
    const duration = parseDuration(tokens[i]);
    if (duration === null) {
      errors.push({ lineNo, message: `unrecognized duration: ${JSON.stringify(tokens[i])}` });
      return null;
    }
    step.durationType = duration.type;
    step.durationTimeS = duration.timeS;
    step.durationDistanceM = duration.distanceM;
    i += 1;
  }

  while (i < tokens.length) {
    const tok = tokens[i];
    const nxt = i + 1 < tokens.length ? tokens[i + 1].toLowerCase() : null;

    const cadenceMatch = CADENCE_RE.exec(tok);
    if (cadenceMatch) {
      step.cadenceLow = parseInt(cadenceMatch[1], 10);
      step.cadenceHigh = cadenceMatch[2] ? parseInt(cadenceMatch[2], 10) : step.cadenceLow;
      i += 1;
      continue;
    }

    if (nxt === "pace") {
      const rangeMatch = PACE_RANGE_RE.exec(tok);
      if (rangeMatch) {
        const a = paceToSpeedMps(rangeMatch[1], rangeMatch[2]);
        const b = paceToSpeedMps(rangeMatch[3], rangeMatch[4]);
        step.targetType = "pace";
        step.targetLow = Math.min(a, b);
        step.targetHigh = Math.max(a, b);
        i += 2;
        continue;
      }
      const singleMatch = PACE_SINGLE_RE.exec(tok);
      if (singleMatch) {
        const speed = paceToSpeedMps(singleMatch[1], singleMatch[2]);
        step.targetType = "pace";
        step.targetLow = speed;
        step.targetHigh = speed;
        i += 2;
        continue;
      }
    }

    if (nxt === "hr") {
      const zoneMatch = HR_ZONE_RE.exec(tok);
      if (zoneMatch) {
        step.targetType = "heart_rate";
        step.targetHrZone = parseInt(zoneMatch[1], 10);
        i += 2;
        continue;
      }
      const rangeMatch = HR_RANGE_RE.exec(tok);
      if (rangeMatch) {
        const lo = parseInt(rangeMatch[1], 10);
        const hi = parseInt(rangeMatch[2], 10);
        step.targetType = "heart_rate";
        step.targetLow = Math.min(lo, hi);
        step.targetHigh = Math.max(lo, hi);
        i += 2;
        continue;
      }
      const singleMatch = HR_SINGLE_RE.exec(tok);
      if (singleMatch) {
        step.targetType = "heart_rate";
        step.targetLow = parseFloat(singleMatch[1]);
        step.targetHigh = step.targetLow;
        i += 2;
        continue;
      }
    }

    errors.push({ lineNo, message: `unrecognized token: ${JSON.stringify(tok)}` });
    i += 1;
  }

  return step;
}

function stepDurationEstimateS(step: ParsedStep): number {
  if (step.durationTimeS !== null) return step.durationTimeS;
  if (step.durationDistanceM !== null) {
    let speed = DEFAULT_ASSUMED_SPEED_MPS;
    if (step.targetType === "pace" && step.targetLow !== null && step.targetHigh !== null) {
      speed = (step.targetLow + step.targetHigh) / 2;
    }
    return speed > 0 ? step.durationDistanceM / speed : 0;
  }
  return 0;
}

function estimateTotalDurationS(steps: ParsedStep[]): number {
  const byIndex = new Map(steps.map((s) => [s.stepIndex, s]));
  const consumed = new Set<number>();
  for (const s of steps) {
    if (s.durationType === "repeat_until_steps_cmplt" && s.repeatFromStep !== null && s.repeatCount !== null) {
      for (let idx = s.repeatFromStep; idx < s.stepIndex; idx++) consumed.add(idx);
    }
  }
  let total = 0;
  for (const s of steps) {
    if (consumed.has(s.stepIndex)) continue;
    if (s.durationType === "repeat_until_steps_cmplt" && s.repeatFromStep !== null && s.repeatCount !== null) {
      let childTotal = 0;
      for (let idx = s.repeatFromStep; idx < s.stepIndex; idx++) {
        const child = byIndex.get(idx);
        if (child) childTotal += stepDurationEstimateS(child);
      }
      total += childTotal * s.repeatCount;
    } else {
      total += stepDurationEstimateS(s);
    }
  }
  return total;
}

/** The instant-preview parse -- never throws on malformed input; unparseable lines/tokens are
 * reported as `ParseError`s while parsing continues past them. See module docstring; the
 * authoritative parse is workout_syntax.py::parse_workout_syntax, run server-side on save. */
export function parseWorkoutSyntax(text: string): ParsedWorkout {
  const lines = text.split("\n");
  const steps: ParsedStep[] = [];
  const errors: ParseError[] = [];
  let stepIndex = 0;
  let i = 0;
  const n = lines.length;

  while (i < n) {
    const rawLine = lines[i].trim();
    if (!rawLine) {
      i += 1;
      continue;
    }
    const [line, comment] = splitComment(rawLine);

    const repeatMatch = REPEAT_MARKER_RE.exec(line);
    if (repeatMatch) {
      const count = parseInt(repeatMatch[1], 10);
      const markerLineNo = i + 1;
      i += 1;
      const childrenStart = stepIndex;
      while (i < n && lines[i].trim()) {
        const [childLine, childComment] = splitComment(lines[i].trim());
        const child = parseStepLine(childLine, i + 1, errors);
        if (child !== null) {
          child.stepIndex = stepIndex;
          child.comment = childComment;
          steps.push(child);
          stepIndex += 1;
        }
        i += 1;
      }
      if (stepIndex === childrenStart) {
        errors.push({ lineNo: markerLineNo, message: `${count}x repeat has no steps` });
      } else {
        const repeatStep = emptyStep(stepIndex);
        repeatStep.durationType = "repeat_until_steps_cmplt";
        repeatStep.repeatFromStep = childrenStart;
        repeatStep.repeatCount = count;
        repeatStep.comment = comment;
        steps.push(repeatStep);
        stepIndex += 1;
      }
      continue;
    }

    const step = parseStepLine(line, i + 1, errors);
    if (step !== null) {
      step.stepIndex = stepIndex;
      step.comment = comment;
      steps.push(step);
      stepIndex += 1;
    }
    i += 1;
  }

  return { steps, errors, estimatedDurationS: estimateTotalDurationS(steps) };
}

// --- Reverse conversion: recorded activity_workout_step rows -> syntax text ------------------
//
// Powers the calendar's "Copy" action on a completed activity (ActivityDetailPage.tsx) -- see
// workout_syntax.py::steps_to_source_text's own docstring for the full rationale. This is the
// version actually wired into the UI (ActivityDetailPage.tsx already has the activity's steps
// loaded client-side); the Python copy exists for dual-implementation test parity.

export interface RecordedStepLike {
  stepIndex: number;
  durationType: string | null;
  durationTimeS: number | null;
  durationDistanceM: number | null;
  targetType: string | null; // only ever "speed" on a recorded step
  targetLowMps: number | null;
  targetHighMps: number | null;
  intensity: string | null;
  repeatFromStep: number | null;
  repeatCount: number | null;
}

function formatDurationToken(
  durationType: string | null,
  timeS: number | null,
  distanceM: number | null,
): string | null {
  if (durationType === "time" && timeS !== null) {
    const total = Math.round(timeS);
    if (total % 60 === 0) return `${total / 60}m`;
    if (total < 60) return `${total}s`;
    return `${Math.floor(total / 60)}m${total % 60}s`;
  }
  if (durationType === "distance" && distanceM !== null) {
    if (distanceM >= 1000) {
      const km = distanceM / 1000;
      const text = km.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
      return `${text}km`;
    }
    return `${Math.round(distanceM)}mtr`;
  }
  if (durationType === "lap_button") {
    // Round-trips back to the same line the athlete typed: "lap", plus the estimate token if one
    // was given. Recursing with "time"/"distance" reuses the formatting above rather than
    // repeating it -- the estimate is an ordinary duration token by construction.
    const estimate =
      timeS !== null
        ? formatDurationToken("time", timeS, null)
        : distanceM !== null
          ? formatDurationToken("distance", null, distanceM)
          : null;
    return estimate === null ? LAP_BUTTON_WORD : `${LAP_BUTTON_WORD} ${estimate}`;
  }
  return null;
}

function formatPaceToken(speedMps: number): string {
  if (speedMps <= 0) return "0:00/km";
  const paceS = 1000 / speedMps;
  let minutes = Math.floor(paceS / 60);
  let seconds = Math.round(paceS % 60);
  if (seconds === 60) {
    minutes += 1;
    seconds = 0;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}/km`;
}

function formatRecordedStepLine(step: RecordedStepLike): string | null {
  const durationTok = formatDurationToken(step.durationType, step.durationTimeS, step.durationDistanceM);
  if (durationTok === null) return null;
  const parts: string[] = [];
  if (step.intensity && INTENSITY_WORDS.has(step.intensity)) {
    parts.push(step.intensity.charAt(0).toUpperCase() + step.intensity.slice(1));
  }
  parts.push(durationTok);
  if (step.targetType === "speed" && step.targetLowMps !== null && step.targetHighMps !== null) {
    const fastPace = formatPaceToken(step.targetHighMps);
    const slowPace = formatPaceToken(step.targetLowMps);
    if (fastPace === slowPace) {
      parts.push(`${fastPace} Pace`);
    } else {
      const fastMinSec = fastPace.split("/")[0];
      parts.push(`${fastMinSec}-${slowPace} Pace`);
    }
  }
  return parts.join(" ");
}

/** The reverse of `parseWorkoutSyntax`: a known set of recorded steps (ordered by stepIndex,
 * unexpanded) back into syntax text, repeat blocks included. Best-effort -- a starting point the
 * athlete edits, not a guaranteed-lossless round trip. */
export function stepsToSourceText(steps: RecordedStepLike[]): string {
  const sorted = [...steps].sort((a, b) => a.stepIndex - b.stepIndex);
  const byIndex = new Map(sorted.map((s) => [s.stepIndex, s]));
  const consumed = new Set<number>();
  for (const s of sorted) {
    if (s.durationType === "repeat_until_steps_cmplt" && s.repeatFromStep !== null) {
      for (let idx = s.repeatFromStep; idx < s.stepIndex; idx++) consumed.add(idx);
    }
  }

  const lines: string[] = [];
  for (const s of sorted) {
    if (consumed.has(s.stepIndex)) continue;
    if (s.durationType === "repeat_until_steps_cmplt" && s.repeatFromStep !== null && s.repeatCount !== null) {
      const children: RecordedStepLike[] = [];
      for (let idx = s.repeatFromStep; idx < s.stepIndex; idx++) {
        const c = byIndex.get(idx);
        if (c) children.push(c);
      }
      const childLines = children.map(formatRecordedStepLine).filter((l): l is string => l !== null);
      if (childLines.length === 0) continue;
      if (lines.length > 0 && lines[lines.length - 1] !== "") lines.push("");
      lines.push(`${s.repeatCount}x`);
      lines.push(...childLines);
      lines.push("");
    } else {
      const line = formatRecordedStepLine(s);
      if (line !== null) lines.push(line);
    }
  }

  while (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  return lines.join("\n");
}

// --- Bridge to the API's own snake_case shape -- lets the live client-side preview (ParsedStep,
// camelCase) reuse workoutSteps.ts's expand/group/label helpers, which already work against
// PlannedWorkoutStepOut (the server's parsed-and-stored shape, snake_case) once a workout is
// saved. Keeping ParsedStep itself camelCase (idiomatic TS) rather than matching the wire format
// is deliberate -- this one small mapping function is a cheaper cost than a camelCase parser
// fighting its own language's conventions everywhere else in this file.
export function parsedStepToApiShape(step: ParsedStep): PlannedWorkoutStepOut {
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
    comment: step.comment,
    // hiit/strength_training only -- a running step (the only kind workout_syntax.py/this file
    // parse) never carries these.
    duration_reps: null,
    exercise_category: null,
    exercise_name: null,
    weight_kg: null,
  };
}
