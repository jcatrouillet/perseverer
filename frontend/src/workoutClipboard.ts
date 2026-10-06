// A plain client-side "copy a workout, paste it onto a date" clipboard for the calendar's own
// Copy/Paste affordance (docs/adr/0015-scheduled-workouts.md). Two writers: a "Copy" action on a
// completed activity (ActivityDetailPage.tsx's CopyWorkoutButton, running-structured activities
// only -- source_text derived from ActivityWorkoutStepOut, which has no exercise fields at all)
// and a "Copy" action on an already-scheduled *planned* workout (WorkoutSummary in
// ScheduleWorkoutForm.tsx, all sport tiers -- the richer source, since a planned workout already
// carries scheduled_time/duration_minutes/steps in the exact shape a paste needs). "Paste" on any
// calendar day (ScheduleWorkoutForm) reads it and restores whichever fields apply to the pasted
// sport. Held in localStorage rather than React state/context so the writers and the reader live
// on genuinely different pages without a router-level provider: a per-viewer convenience (this
// browser, this tab's storage), not data that needs to survive across devices or be visible to
// Claude/the backend -- same "per-viewer convenience" category as every other localStorage use in
// this app.
import type { PlannedWorkoutStepOut } from "./api/types";

const STORAGE_KEY = "perseverer_workout_clipboard";

export interface WorkoutClipboardItem {
  sport: string;
  name: string | null;
  source_text: string | null;
  // "HH:MM" (24h) -- yoga/bouldering/hiit/strength_training's display-only time of day. Absent
  // (not just null) from a copy off a completed activity, which has no such concept.
  scheduled_time?: string | null;
  // yoga/bouldering only -- see PlannedWorkoutIn. Absent from a copy off a completed activity.
  duration_minutes?: number | null;
  // hiit/strength_training only -- the already-structured steps of the copied planned workout,
  // in the same *Out shape `GET /planned-workouts/{date}` returns (reused rather than converting
  // to *In shape at copy time, since apiStepsToEntries -- the hydration helper ScheduleWorkoutForm
  // already uses for Edit -- takes exactly this shape). Absent from a copy off a completed
  // activity: recorded FIT-parsed steps carry no exercise_category/exercise_name/weight_kg at
  // all. Also how a per-step comment travels for hiit/strength_training -- it's already part of
  // each step's own `comment` field, no separate clipboard handling needed (running's own
  // per-step comments are embedded inline in `source_text` above instead).
  steps?: PlannedWorkoutStepOut[] | null;
  // The workout's own general-guidance comment (running/hiit/strength_training only) -- absent
  // from a copy off a completed activity, which has no such concept.
  comment?: string | null;
}

export function copyWorkoutToClipboard(item: WorkoutClipboardItem): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(item));
  } catch {
    // Best-effort -- a private window or blocked site data just means Paste won't find
    // anything, not a crash.
  }
}

export function readWorkoutClipboard(): WorkoutClipboardItem | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as WorkoutClipboardItem;
  } catch {
    return null;
  }
}
