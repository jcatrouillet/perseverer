// A plain client-side "copy a workout, paste it onto a date" clipboard for the calendar's own
// Copy/Paste affordance (docs/adr/0015-scheduled-workouts.md) -- a "Copy" action on a completed
// activity (ActivityDetailPage.tsx) writes here; "Paste" on any calendar day (ScheduleWorkoutForm)
// reads it. Held in localStorage rather than React state/context so the two live on genuinely
// different pages without a router-level provider: a per-viewer convenience (this browser, this
// tab's storage), not data that needs to survive across devices or be visible to Claude/the
// backend -- same "per-viewer convenience" category as every other localStorage use in this app.
const STORAGE_KEY = "perseverer_workout_clipboard";

export interface WorkoutClipboardItem {
  sport: string;
  name: string | null;
  source_text: string | null;
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

export function clearWorkoutClipboard(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // best-effort, see copyWorkoutToClipboard
  }
}
