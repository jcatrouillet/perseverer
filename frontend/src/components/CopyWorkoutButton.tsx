// "Copy" half of the calendar's copy/paste scheduling affordance (docs/adr/0015-scheduled-
// workouts.md) -- writes this activity's own structure into the workout clipboard
// (workoutClipboard.ts) as workout-syntax text, ready for "Paste" on any calendar day cell
// (ScheduleWorkoutForm.tsx). A one-time starting point the athlete edits before saving, never a
// live link back to this activity.
import { useState } from "react";

import type { ActivityWorkoutStepOut } from "../api/types";
import { copyWorkoutToClipboard } from "../workoutClipboard";
import { type RecordedStepLike, stepsToSourceText } from "../workoutSyntax";

function toRecordedStepLike(s: ActivityWorkoutStepOut): RecordedStepLike {
  return {
    stepIndex: s.step_index,
    durationType: s.duration_type,
    durationTimeS: s.duration_time_s,
    durationDistanceM: s.duration_distance_m,
    targetType: s.target_type,
    targetLowMps: s.target_low_mps,
    targetHighMps: s.target_high_mps,
    intensity: s.intensity,
    repeatFromStep: s.repeat_from_step,
    repeatCount: s.repeat_count,
  };
}

export function CopyWorkoutButton({
  sport,
  name,
  steps,
}: {
  sport: string;
  name: string | null;
  // undefined while still loading, null/empty when this activity has no recorded structure --
  // either way the copy still works, just with name/sport only (see stepsToSourceText's own
  // "best-effort, never a guaranteed-lossless round trip" contract).
  steps: ActivityWorkoutStepOut[] | null | undefined;
}) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    const sourceText = steps && steps.length > 0 ? stepsToSourceText(steps.map(toRecordedStepLike)) : null;
    copyWorkoutToClipboard({ sport, name, source_text: sourceText || null });
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <button type="button" className="button" onClick={handleCopy}>
      {copied ? "Copied — paste it on a calendar day" : "Copy workout"}
    </button>
  );
}
