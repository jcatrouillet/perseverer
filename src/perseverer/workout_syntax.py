"""The scheduled-workout text syntax -- inspired by intervals.icu's own workout-builder syntax
(https://forum.intervals.icu/t/workout-builder-syntax-quick-guide/123701), not a full
implementation of it: Perseverer supports the subset that actually applies to running (this
feature's "running first" scope, see docs/adr/0015-scheduled-workouts.md) -- duration (time or
distance), a pace or heart-rate target (absolute range, a single value, or a `Z<n>` zone
resolved against the athlete's own `athlete_hr_zone_config` at push time -- see
`hr_zones.py::resolve_hr_zone_bpm`, not resolved here), trailing running cadence, and a simple
`Nx` repeat block. **Deliberately not implemented**: power targets/zones (no power meter data in
this app), ramps, freeride, MMP, and timed text prompts -- none of these map onto anything
Perseverer tracks or onto Garmin's own workout-step model for this use case.

Duplicated in `frontend/src/workoutSyntax.ts` for instant client-side preview as the athlete
types, same cross-language-duplication precedent as `gap.ts`/`gap.py`
(`frontend/src/gap.ts`/`src/perseverer/gap.py`) and `weatherCode.ts`/`weather_code.py` -- kept in
sync via a shared fixture table (`tests/workout_syntax_fixtures.py`, mirrored by
`frontend/src/workoutSyntax.fixtures.ts`) exercised by both languages' test suites rather than
trusted to agree by inspection. This module is the *authoritative* parse (run server-side on
every save, feeding both `planned_workout_step` storage and the Garmin workout JSON builder in
`adapters/garmin_connect.py`); the TS copy is a preview only.

One line is one step. A standalone `<N>x` line starts a repeat block: every non-blank line that
follows, up to the next blank line, is one of the block's `repeat_count` children -- the exact
same "children rows precede the block's own summarizing row, addressed by
[repeat_from_step, step_index)" convention `activity_workout_step` already uses (see
`fit/parser.py`'s own docstring), reused deliberately so the frontend's existing
`expandWorkoutSteps`/`groupWorkoutStepsForDisplay` (`workoutSteps.ts`) render either table's rows
unmodified.

Grammar for a single (non-repeat-marker) line, space-separated tokens, all keywords
case-insensitive:

    [intensity] duration [target] [cadence]

- `intensity`: one of warmup/cooldown/recovery/rest/active -- the same vocabulary
  `activity_workout_step.intensity` already uses. Optional; omitted means "no particular
  intensity label" (a plain interval/main-set step).
- `duration` (required): `10m` / `5m30s` / `90s` (time) or `2km` / `800mtr` / `1mi` (distance) --
  bare `m` means *minutes*, `mtr` means *meters* (matching intervals.icu's own convention
  exactly, to avoid a real ambiguity trap between the two).
- `target` (optional): a pace value/range immediately followed by the word `Pace`
  (`5:10/km Pace` or `5:00-5:20/km Pace`), or a heart-rate value/range/zone immediately followed
  by the word `HR` (`150 HR`, `140-150 HR`, or `Z2 HR`).
- `cadence` (optional): a single trailing token, `170-180spm` (a range) or `175spm` (a single
  value) -- spm (steps/min), not intervals.icu's cycling-oriented "rpm" label.

Target and cadence tokens are recognized wherever they appear after the duration (order between
them doesn't matter); an unrecognized token records a `ParseError` for that line but does not
stop the line's step from being emitted with whatever *was* understood -- better to show a
partial live preview than nothing, matching intervals.icu's own forgiving-editor UX.

A trailing `# comment text` on any line (a step line or a standalone `<N>x` repeat-marker line)
attaches a freeform note to that specific step/block -- stored verbatim on
`planned_workout_step.comment`, never parsed further, never sent to Garmin. Only the first `#`
starts the comment; anything after it, including further `#` characters, is comment text. A line
that is *only* a comment (nothing left once the `#...` part is removed) is not a supported
feature -- it falls through to the ordinary "missing duration" error, same as any other
content-free line, since a comment is only ever an annotation on a real step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Same vocabulary activity_workout_step.intensity already uses.
INTENSITY_WORDS = frozenset({"warmup", "cooldown", "recovery", "rest", "active"})

_REPEAT_MARKER_RE = re.compile(r"^(\d+)\s*[xX]$")

_DIST_KM_RE = re.compile(r"^(\d+(?:\.\d+)?)km$", re.IGNORECASE)
_DIST_MTR_RE = re.compile(r"^(\d+(?:\.\d+)?)mtr$", re.IGNORECASE)
_DIST_MI_RE = re.compile(r"^(\d+(?:\.\d+)?)mi$", re.IGNORECASE)
_TIME_MS_RE = re.compile(r"^(\d+)m(\d+)s$", re.IGNORECASE)
_TIME_M_RE = re.compile(r"^(\d+)m$", re.IGNORECASE)
_TIME_S_RE = re.compile(r"^(\d+)s$", re.IGNORECASE)

_PACE_RANGE_RE = re.compile(r"^(\d+):(\d{2})-(\d+):(\d{2})/km$")
_PACE_SINGLE_RE = re.compile(r"^(\d+):(\d{2})/km$")
_HR_ZONE_RE = re.compile(r"^Z(\d)$", re.IGNORECASE)
_HR_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
_HR_SINGLE_RE = re.compile(r"^(\d+)$")
_CADENCE_RE = re.compile(r"^(\d+)(?:-(\d+))?spm$", re.IGNORECASE)

_MILE_IN_METERS = 1609.34
# Used only to turn a distance-based step into an *estimated* duration for
# estimated_duration_s (display only -- the real duration is whatever pace the athlete actually
# runs). A moderate easy-running speed, used when no pace target is available to average instead.
DEFAULT_ASSUMED_SPEED_MPS = 3.0


@dataclass
class ParsedStep:
    step_index: int
    duration_type: str | None = None  # "time" | "distance" | "repeat_until_steps_cmplt"
    duration_time_s: float | None = None
    duration_distance_m: float | None = None
    target_type: str | None = None  # "pace" | "heart_rate"
    target_low: float | None = None  # m/s for pace, bpm for heart_rate
    target_high: float | None = None
    target_hr_zone: int | None = None
    cadence_low: int | None = None
    cadence_high: int | None = None
    intensity: str | None = None
    repeat_from_step: int | None = None
    repeat_count: int | None = None
    comment: str | None = None


@dataclass
class ParseError:
    line_no: int  # 1-indexed, matching how an editor shows line numbers
    message: str


@dataclass
class ParsedWorkout:
    steps: list[ParsedStep]
    errors: list[ParseError]
    estimated_duration_s: float


def _parse_duration(token: str) -> tuple[str, float | None, float | None] | None:
    """Returns (duration_type, duration_time_s, duration_distance_m) -- exactly one of the two
    values is non-None, matching duration_type ("time" or "distance"). `None` if `token` isn't a
    recognized duration."""
    if m := _DIST_KM_RE.match(token):
        return ("distance", None, float(m.group(1)) * 1000.0)
    if m := _DIST_MTR_RE.match(token):
        return ("distance", None, float(m.group(1)))
    if m := _DIST_MI_RE.match(token):
        return ("distance", None, float(m.group(1)) * _MILE_IN_METERS)
    if m := _TIME_MS_RE.match(token):
        return ("time", float(m.group(1)) * 60 + float(m.group(2)), None)
    if m := _TIME_M_RE.match(token):
        return ("time", float(m.group(1)) * 60, None)
    if m := _TIME_S_RE.match(token):
        return ("time", float(m.group(1)), None)
    return None


def _pace_to_speed_mps(minutes: str, seconds: str) -> float:
    total_s = int(minutes) * 60 + int(seconds)
    if total_s <= 0:
        return 0.0
    return 1000.0 / total_s


def _split_comment(line: str) -> tuple[str, str | None]:
    """Splits a trailing '# comment text' off a line -- only the FIRST '#' starts the comment;
    further '#' characters are just part of the comment text. No '#' at all -> (line, None)."""
    if "#" not in line:
        return line, None
    step_part, _, comment_part = line.partition("#")
    comment = comment_part.strip()
    return step_part.rstrip(), comment or None


def _parse_step_line(line: str, *, line_no: int, errors: list[ParseError]) -> ParsedStep | None:
    tokens = line.split()
    i = 0
    intensity: str | None = None
    if tokens and tokens[0].lower() in INTENSITY_WORDS:
        intensity = tokens[0].lower()
        i = 1

    if i >= len(tokens):
        errors.append(ParseError(line_no=line_no, message=f"missing duration: {line!r}"))
        return None
    duration = _parse_duration(tokens[i])
    if duration is None:
        errors.append(
            ParseError(line_no=line_no, message=f"unrecognized duration: {tokens[i]!r}")
        )
        return None
    duration_type, duration_time_s, duration_distance_m = duration
    i += 1

    target_type: str | None = None
    target_low: float | None = None
    target_high: float | None = None
    target_hr_zone: int | None = None
    cadence_low: int | None = None
    cadence_high: int | None = None

    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else None

        if m := _CADENCE_RE.match(tok):
            cadence_low = int(m.group(1))
            cadence_high = int(m.group(2)) if m.group(2) else cadence_low
            i += 1
            continue

        if nxt == "pace" and (m := _PACE_RANGE_RE.match(tok)):
            speed_a = _pace_to_speed_mps(m.group(1), m.group(2))
            speed_b = _pace_to_speed_mps(m.group(3), m.group(4))
            target_type = "pace"
            target_low, target_high = min(speed_a, speed_b), max(speed_a, speed_b)
            i += 2
            continue
        if nxt == "pace" and (m := _PACE_SINGLE_RE.match(tok)):
            speed = _pace_to_speed_mps(m.group(1), m.group(2))
            target_type = "pace"
            target_low = target_high = speed
            i += 2
            continue

        if nxt == "hr" and (m := _HR_ZONE_RE.match(tok)):
            target_type = "heart_rate"
            target_hr_zone = int(m.group(1))
            i += 2
            continue
        if nxt == "hr" and (m := _HR_RANGE_RE.match(tok)):
            lo, hi = int(m.group(1)), int(m.group(2))
            target_type = "heart_rate"
            target_low, target_high = float(min(lo, hi)), float(max(lo, hi))
            i += 2
            continue
        if nxt == "hr" and (m := _HR_SINGLE_RE.match(tok)):
            target_type = "heart_rate"
            target_low = target_high = float(m.group(1))
            i += 2
            continue

        errors.append(ParseError(line_no=line_no, message=f"unrecognized token: {tok!r}"))
        i += 1

    return ParsedStep(
        step_index=-1,  # assigned by the caller once the step's final position is known
        duration_type=duration_type,
        duration_time_s=duration_time_s,
        duration_distance_m=duration_distance_m,
        target_type=target_type,
        target_low=target_low,
        target_high=target_high,
        target_hr_zone=target_hr_zone,
        cadence_low=cadence_low,
        cadence_high=cadence_high,
        intensity=intensity,
    )


def step_target_speed_mps(step: ParsedStep) -> float | None:
    """The step's own target pace, averaged, in m/s -- `None` if it has no pace target (a
    heart-rate-targeted or untargeted step gives no speed to derive a distance/duration estimate
    from either way). Public: also used by `planned_workout_stats.py` for zone bucketing/load."""
    if step.target_type == "pace" and step.target_low is not None and step.target_high is not None:
        return (step.target_low + step.target_high) / 2
    return None


def _step_duration_estimate_s(step: ParsedStep) -> float:
    if step.duration_time_s is not None:
        return step.duration_time_s
    if step.duration_distance_m is not None:
        speed = step_target_speed_mps(step) or DEFAULT_ASSUMED_SPEED_MPS
        return step.duration_distance_m / speed if speed > 0 else 0.0
    return 0.0


def estimate_step_distance_m(step: ParsedStep) -> float:
    """Mirrors `_step_duration_estimate_s` for the other axis -- exact when the step's own
    duration is already distance-based, estimated (same target-pace-or-default-speed source)
    when it's time-based. Public: also used by `planned_workout_stats.py` to total a whole
    workout's estimated distance."""
    if step.duration_distance_m is not None:
        return step.duration_distance_m
    if step.duration_time_s is not None:
        speed = step_target_speed_mps(step) or DEFAULT_ASSUMED_SPEED_MPS
        return step.duration_time_s * speed
    return 0.0


def expand_repeat_groups(steps: list[ParsedStep]) -> list[ParsedStep]:
    """Flattens repeat-group marker rows into `repeat_count` literal copies of their own
    children, in the order the workout is actually executed -- a standalone step keeps its own
    single place in the sequence; a `4x` block of 2 children becomes 8 concrete steps
    (children, children, children, children), not 2 steps with a multiplier attached. Public:
    used both by `_estimate_total_duration_s` below (sum over the flattened list) and by
    `planned_workout_stats.py` (one visual segment per flattened step, so a repeated interval
    reads as N equal-width blocks in the load bar, not one block scaled up). Any number of
    independent repeat blocks in one workout is supported, each addressed by its own
    `repeat_from_step` -- same assumption `build_workout_segment`
    (`planned_workouts.py`)/`workoutSteps.ts::expandWorkoutSteps` already make."""
    by_index = {s.step_index: s for s in steps}
    markers: dict[int, ParsedStep] = {
        s.repeat_from_step: s
        for s in steps
        if s.duration_type == "repeat_until_steps_cmplt"
        and s.repeat_from_step is not None
        and s.repeat_count is not None
    }
    consumed: set[int] = set()
    for marker in markers.values():
        assert marker.repeat_from_step is not None  # narrows for mypy; guaranteed by the dict above
        consumed.update(range(marker.repeat_from_step, marker.step_index))

    result: list[ParsedStep] = []
    for s in sorted(steps, key=lambda s: s.step_index):
        if s.duration_type == "repeat_until_steps_cmplt":
            continue  # a marker row is never itself a real, executable step
        if s.step_index not in consumed:
            result.append(s)
            continue
        block = markers.get(s.step_index)
        if block is None:
            continue  # a later child of a block already expanded when its first child was hit
        assert block.repeat_from_step is not None and block.repeat_count is not None
        child_range = range(block.repeat_from_step, block.step_index)
        children = [by_index[i] for i in child_range if i in by_index]
        for _ in range(block.repeat_count):
            result.extend(children)
    return result


def _estimate_total_duration_s(steps: list[ParsedStep]) -> float:
    return sum(_step_duration_estimate_s(s) for s in expand_repeat_groups(steps))


def parse_workout_syntax(text: str) -> ParsedWorkout:
    """The authoritative parse -- run server-side on every save (`api/routers/
    planned_workouts.py`). Never raises on malformed input: unparseable lines/tokens are
    reported as `ParseError`s (surfaced back to the athlete for the textarea to underline), and
    parsing continues past them rather than aborting the whole save."""
    lines = text.splitlines()
    steps: list[ParsedStep] = []
    errors: list[ParseError] = []
    step_index = 0
    i = 0
    n = len(lines)
    while i < n:
        raw_line = lines[i].strip()
        if not raw_line:
            i += 1
            continue
        line, comment = _split_comment(raw_line)

        if m := _REPEAT_MARKER_RE.match(line):
            count = int(m.group(1))
            marker_line_no = i + 1
            i += 1
            children_start = step_index
            while i < n and lines[i].strip():
                child_line, child_comment = _split_comment(lines[i].strip())
                child = _parse_step_line(child_line, line_no=i + 1, errors=errors)
                if child is not None:
                    child.step_index = step_index
                    child.comment = child_comment
                    steps.append(child)
                    step_index += 1
                i += 1
            if step_index == children_start:
                errors.append(
                    ParseError(line_no=marker_line_no, message=f"{count}x repeat has no steps")
                )
            else:
                steps.append(
                    ParsedStep(
                        step_index=step_index,
                        duration_type="repeat_until_steps_cmplt",
                        repeat_from_step=children_start,
                        repeat_count=count,
                        comment=comment,
                    )
                )
                step_index += 1
            continue

        step = _parse_step_line(line, line_no=i + 1, errors=errors)
        if step is not None:
            step.step_index = step_index
            step.comment = comment
            steps.append(step)
            step_index += 1
        i += 1

    return ParsedWorkout(
        steps=steps, errors=errors, estimated_duration_s=_estimate_total_duration_s(steps)
    )


# --- Reverse conversion: recorded activity_workout_step rows -> syntax text ------------------
#
# Powers the calendar's "Copy" action on a completed activity (ActivityDetailPage.tsx): turns a
# real recorded structured workout back into editable syntax text as a one-time starting point,
# never a link back to the source (see docs/adr/0015-scheduled-workouts.md). The actual UI path
# runs the TS twin of this function (frontend/src/workoutSyntax.ts::stepsToSourceText) client-
# side, since ActivityDetailPage.tsx already has the activity's steps loaded; this Python copy
# exists so the conversion is tested the same dual-implementation way as the forward parse (see
# module docstring), and so a future server-side "copy" affordance (e.g. from the MCP server)
# doesn't need a third implementation.


@dataclass
class RecordedStepLike:
    """The subset of `activity_workout_step`'s columns this conversion actually reads --
    accepting a lightweight dataclass here (rather than the SQLAlchemy Row itself) keeps this
    function testable with plain fixtures and reusable from any caller, not just one shaped
    exactly like a DB row."""

    step_index: int
    duration_type: str | None
    duration_time_s: float | None
    duration_distance_m: float | None
    target_type: str | None  # only ever "speed" on a recorded step (see activity_workout_step)
    target_low_mps: float | None
    target_high_mps: float | None
    intensity: str | None
    repeat_from_step: int | None
    repeat_count: int | None


def _format_duration_token(
    duration_type: str | None, time_s: float | None, distance_m: float | None
) -> str | None:
    if duration_type == "time" and time_s is not None:
        total = round(time_s)
        if total % 60 == 0:
            return f"{total // 60}m"
        if total < 60:
            return f"{total}s"
        return f"{total // 60}m{total % 60}s"
    if duration_type == "distance" and distance_m is not None:
        if distance_m >= 1000:
            km = distance_m / 1000.0
            text = f"{km:.2f}".rstrip("0").rstrip(".")
            return f"{text}km"
        text = f"{distance_m:.0f}"
        return f"{text}mtr"
    return None


def _format_pace_token(speed_mps: float) -> str:
    if speed_mps <= 0:
        return "0:00/km"
    pace_s = 1000.0 / speed_mps
    minutes = int(pace_s // 60)
    seconds = round(pace_s % 60)
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}/km"


def _format_recorded_step_line(step: RecordedStepLike) -> str | None:
    duration_tok = _format_duration_token(
        step.duration_type, step.duration_time_s, step.duration_distance_m
    )
    if duration_tok is None:
        return None
    parts: list[str] = []
    if step.intensity and step.intensity in INTENSITY_WORDS:
        parts.append(step.intensity.capitalize())
    parts.append(duration_tok)
    if (
        step.target_type == "speed"
        and step.target_low_mps is not None
        and step.target_high_mps is not None
    ):
        # target_high_mps is the *faster* bound (higher speed -> lower pace time) -- same
        # "faster first" convention frontend/src/workoutSteps.ts::targetPaceRangeLabel uses.
        fast_pace = _format_pace_token(step.target_high_mps)  # "M:SS/km"
        slow_pace = _format_pace_token(step.target_low_mps)
        if fast_pace == slow_pace:
            parts.append(f"{fast_pace} Pace")
        else:
            fast_min_sec = fast_pace.split("/")[0]
            parts.append(f"{fast_min_sec}-{slow_pace} Pace")
    return " ".join(parts)


def steps_to_source_text(steps: list[RecordedStepLike]) -> str:
    """The reverse of `parse_workout_syntax`: a known set of recorded steps (already ordered by
    `step_index`, unexpanded -- same convention as everywhere else in this module) back into
    syntax text, repeat blocks included. Steps this function can't represent (no recognizable
    duration) are silently skipped rather than raising -- a best-effort starting point the
    athlete edits anyway, not a guaranteed-lossless round trip."""
    sorted_steps = sorted(steps, key=lambda s: s.step_index)
    by_index = {s.step_index: s for s in sorted_steps}
    consumed: set[int] = set()
    for s in sorted_steps:
        if s.duration_type == "repeat_until_steps_cmplt" and s.repeat_from_step is not None:
            consumed.update(range(s.repeat_from_step, s.step_index))

    lines: list[str] = []
    for s in sorted_steps:
        if s.step_index in consumed:
            continue
        if (
            s.duration_type == "repeat_until_steps_cmplt"
            and s.repeat_from_step is not None
            and s.repeat_count is not None
        ):
            children = [
                by_index[i] for i in range(s.repeat_from_step, s.step_index) if i in by_index
            ]
            child_lines = [ln for c in children if (ln := _format_recorded_step_line(c))]
            if not child_lines:
                continue
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"{s.repeat_count}x")
            lines.extend(child_lines)
            lines.append("")
        else:
            line = _format_recorded_step_line(s)
            if line is not None:
                lines.append(line)

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)
