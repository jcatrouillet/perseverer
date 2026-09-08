// The "Planned workout" section of MonthView's expanded-day card and DayViewPage
// (docs/adr/0015-scheduled-workouts.md): shows every workout scheduled for a date (a day can
// hold more than one, each independently id-addressed) + its push status, or a "Schedule a
// workout" affordance if none exist yet. Three sport tiers, matching
// planned_workouts.py::save_planned_workout: running gets the full text-syntax editor + live
// preview; yoga/bouldering (PLACEHOLDER_SPORTS) are deliberately simpler placeholders -- a
// duration (minutes) + a time-of-day field, no structured syntax at all, per the user's own
// explicit scoping ("no structured text syntax needed, it's just to put placeholder for those
// sports"); hiit/strength_training (EXERCISE_SPORTS) get the real exercise picker
// (ExerciseStepEditor) -- the athlete's own choice over a simpler placeholder or a free-text
// syntax. All of these push to Garmin -- "fitness" (no structured syntax and no placeholder
// builder either) was dropped from the sport list entirely rather than kept as a dead option.
import { useEffect, useRef, useState, type ReactNode } from "react";

import {
  useCompletePlannedWorkout,
  useCreatePlannedWorkout,
  useCreateRecurringPlannedWorkouts,
  useDeletePlannedWorkout,
  usePlannedWorkoutsForDate,
  usePushPlannedWorkout,
  useUncompletePlannedWorkout,
  useUpdatePlannedWorkout,
} from "../api/queries";
import type { PlannedWorkoutOut, PlannedWorkoutStepOut } from "../api/types";
import {
  apiStepsToItems,
  estimateItemsDurationS,
  ExerciseStepEditor,
  itemsToApiSteps,
  preloadExerciseCatalog,
  type ExerciseItem,
} from "./ExerciseStepEditor";
import {
  formatStepDurationLabel,
  groupWorkoutStepsForDisplay,
  plannedCadenceLabel,
  plannedExerciseLabel,
  plannedTargetLabel,
  type WorkoutDisplayGroup,
} from "../workoutSteps";
import { parsedStepToApiShape, parseWorkoutSyntax } from "../workoutSyntax";
import { copyWorkoutToClipboard, readWorkoutClipboard } from "../workoutClipboard";
import { Icon } from "./Icon";
import { LoadingSpinner } from "./LoadingSpinner";
import { plannedWorkoutSportStyle } from "../metricStyle";
import { StepBuilderModal } from "./StepBuilderModal";
import { WorkoutLoadBar } from "./WorkoutLoadBar";
import "../styles/plannedWorkout.css";

const SPORTS = [
  { value: "running", label: "Running" },
  { value: "yoga", label: "Yoga" },
  { value: "bouldering", label: "Bouldering" },
  { value: "hiit", label: "HIIT" },
  { value: "strength_training", label: "Strength training" },
];

// Every sport in SPORTS above has a real Garmin push path.
const PUSHABLE_SPORTS = new Set(["running", "yoga", "bouldering", "hiit", "strength_training"]);
// Sports with no structured syntax at all -- just a name, a duration, and a time of day. Mirrors
// planned_workouts.py::PLACEHOLDER_SPORTS exactly.
const PLACEHOLDER_SPORTS = new Set(["yoga", "bouldering"]);
// Real, named Garmin exercises picked from the catalog. Mirrors planned_workouts.py::
// EXERCISE_SPORTS exactly.
const EXERCISE_SPORTS = new Set(["hiit", "strength_training"]);

// usePlannedWorkoutsForDate only ever returns real, saved rows (never the old
// available:false/id:null sentinel a single-object GET used to return for absence -- a
// nonexistent workout is now just an entry missing from the list) so every element genuinely
// has a non-null id; this narrows that once at the API boundary instead of guarding it at every
// call site below.
type ScheduledWorkout = PlannedWorkoutOut & { id: number };

function statusLabel(status: PlannedWorkoutOut["push_status"]): string {
  if (status === "pushed") return "Pushed to Garmin";
  if (status === "push_failed") return "Push failed";
  return "Draft";
}

// The Warmup/5x[...]/Cooldown step list -- shared by the running text-syntax editor's own live
// parse preview (below) and WorkoutSummary's read-only step detail (also used for hiit/
// strength_training there, isExercise=true prefixing each line with its exercise name and
// appending its weight, since those steps carry no target/cadence of their own to show
// instead). One renderer, so the live preview and the saved-workout summary can never drift
// apart in how they format the same step.
function WorkoutStepGroups({
  groups,
  isExercise,
  footer,
}: {
  groups: WorkoutDisplayGroup<PlannedWorkoutStepOut>[];
  isExercise: boolean;
  /** Rendered inside the same bordered box, after the groups -- e.g. the live-edit form's own
   * "Estimated duration" note, which only applies while actively typing/previewing. */
  footer?: ReactNode;
}) {
  if (groups.length === 0) return null;
  return (
    <div className="planned-workout-form__preview">
      {groups.map((g, i) => (
        <div key={i} className="planned-workout-form__preview-group">
          <strong>{g.label}</strong>
          <ul>
            {g.steps.map((s, j) => (
              <li key={j}>
                {isExercise && `${plannedExerciseLabel(s)} `}
                {formatStepDurationLabel(s)}
                {plannedTargetLabel(s) ? ` @ ${plannedTargetLabel(s)}` : ""}
                {plannedCadenceLabel(s) ? ` · ${plannedCadenceLabel(s)}` : ""}
                {isExercise && s.weight_kg ? ` @ ${s.weight_kg}kg` : ""}
                {s.comment ? ` — ${s.comment}` : ""}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {footer}
    </div>
  );
}

// Sports with no structured syntax at all -- just a name, a duration, and a time of day. Mirrors
// planned_workouts.py::PLACEHOLDER_SPORTS exactly. Declared here (ahead of its first use in
// WorkoutSummary) since the later, form-scoped declaration below is for the editing form only.
const DETAIL_PLACEHOLDER_SPORTS = new Set(["yoga", "bouldering"]);
const DETAIL_EXERCISE_SPORTS = new Set(["hiit", "strength_training"]);

/** Read-only step-by-step detail for a saved workout -- running and hiit/strength_training via
 * the shared WorkoutStepGroups grouping above (steps arrive structured either way, see
 * db/schema.py::planned_workout_step's own docstring); yoga/bouldering have no structured steps
 * at all, so their own freeform source_text notes are shown verbatim instead. Absent entirely
 * for a workout with nothing to show (no steps, no notes). */
function WorkoutDetails({ workout }: { workout: ScheduledWorkout }) {
  if (workout.sport == null) return null;
  if (DETAIL_PLACEHOLDER_SPORTS.has(workout.sport)) {
    if (!workout.source_text) return null;
    return <p className="chart-note planned-workout__notes">{workout.source_text}</p>;
  }
  if (workout.steps.length === 0) return null;
  return (
    <WorkoutStepGroups
      groups={groupWorkoutStepsForDisplay(workout.steps)}
      isExercise={DETAIL_EXERCISE_SPORTS.has(workout.sport)}
    />
  );
}

function formatDurationMinutes(estimatedDurationS: number | null): string | null {
  if (estimatedDurationS == null || estimatedDurationS <= 0) return null;
  return `${Math.round(estimatedDurationS / 60)} min`;
}

function WorkoutSummary({ workout }: { workout: ScheduledWorkout }) {
  const del = useDeletePlannedWorkout();
  const push = usePushPlannedWorkout();
  const complete = useCompletePlannedWorkout();
  const uncomplete = useUncompletePlannedWorkout();
  const duration = formatDurationMinutes(workout.estimated_duration_s);
  const [copied, setCopied] = useState(false);
  const isDone = workout.completed_at != null;

  function handleCopy() {
    copyWorkoutToClipboard({
      sport: workout.sport ?? "running",
      name: workout.name,
      source_text: workout.source_text,
      scheduled_time: workout.scheduled_time,
      duration_minutes:
        workout.estimated_duration_s != null
          ? Math.round(workout.estimated_duration_s / 60)
          : null,
      steps: workout.steps,
    });
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="planned-workout__summary">
      <div className="planned-workout__summary-header">
        {workout.sport != null && <Icon name={plannedWorkoutSportStyle(workout.sport).icon} />}
        <strong>{workout.name || workout.sport}</strong>
        <span className={`planned-workout__status planned-workout__status--${workout.push_status}`}>
          {statusLabel(workout.push_status)}
        </span>
        {isDone && (
          <span className="planned-workout__status planned-workout__status--completed">Done</span>
        )}
      </div>
      {(workout.scheduled_time || duration) && (
        <p className="chart-note">
          {workout.scheduled_time}
          {workout.scheduled_time && duration && " · "}
          {duration}
        </p>
      )}
      <WorkoutLoadBar workout={workout} />
      <WorkoutDetails workout={workout} />
      {workout.push_error && (
        <p className="chart-note" role="alert">
          {workout.push_error}
        </p>
      )}
      <div className="planned-workout__actions">
        {workout.sport != null && PUSHABLE_SPORTS.has(workout.sport) && (
          <button
            type="button"
            className="button"
            onClick={() => push.mutate(workout.id)}
            disabled={push.isPending}
          >
            {push.isPending ? "Pushing…" : "Push to Garmin"}
          </button>
        )}
        <button
          type="button"
          className="button"
          onClick={() => (isDone ? uncomplete.mutate(workout.id) : complete.mutate(workout.id))}
          disabled={complete.isPending || uncomplete.isPending}
        >
          {isDone ? "Mark as not done" : "Mark as done"}
        </button>
        <button type="button" className="button" onClick={handleCopy}>
          {copied ? "Copied — paste it on another day" : "Copy"}
        </button>
        <button
          type="button"
          className="button"
          onClick={() => del.mutate(workout.id)}
          disabled={del.isPending}
        >
          Delete
        </button>
      </div>
    </div>
  );
}

function WorkoutEditForm({
  localDate,
  workoutId,
  initial,
  pasteOnMount,
  onDone,
}: {
  localDate: string;
  workoutId: number | null; // null means "creating a new workout on this date"
  initial?: PlannedWorkoutOut;
  pasteOnMount?: boolean;
  onDone: () => void;
}) {
  const create = useCreatePlannedWorkout();
  const update = useUpdatePlannedWorkout();
  const recurring = useCreateRecurringPlannedWorkouts();

  // Kicks off the (code-split, ~230KB) exercise catalog fetch as soon as this day's panel
  // expands, well before the athlete might pick hiit/strength_training -- see
  // ExerciseStepEditor.tsx's own docstring for why it's dynamically imported at all.
  useEffect(() => {
    void preloadExerciseCatalog();
  }, []);

  const [sport, setSport] = useState(initial?.sport ?? "running");
  const [name, setName] = useState(initial?.name ?? "");
  const [sourceText, setSourceText] = useState(initial?.source_text ?? "");
  const [durationMinutes, setDurationMinutes] = useState(
    initial?.estimated_duration_s
      ? String(Math.round(initial.estimated_duration_s / 60))
      : "",
  );
  const [scheduledTime, setScheduledTime] = useState(initial?.scheduled_time ?? "");
  const [stepBuilderOpen, setStepBuilderOpen] = useState(false);
  const [exerciseItems, setExerciseItems] = useState<ExerciseItem[]>(
    initial != null && EXERCISE_SPORTS.has(initial.sport ?? "")
      ? apiStepsToItems(initial.steps)
      : [],
  );
  const [showRecurrence, setShowRecurrence] = useState(false);
  const [recurFrequency, setRecurFrequency] = useState<"weekly" | "every_n_days" | "monthly">(
    "weekly",
  );
  const [recurIntervalDays, setRecurIntervalDays] = useState("2");
  const [recurStopMode, setRecurStopMode] = useState<"count" | "until">("count");
  const [recurCount, setRecurCount] = useState("4");
  const [recurUntil, setRecurUntil] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const clipboardItem = readWorkoutClipboard();
  const isPlaceholderSport = PLACEHOLDER_SPORTS.has(sport);
  const isExerciseSport = EXERCISE_SPORTS.has(sport);

  function handlePaste() {
    const item = readWorkoutClipboard();
    if (!item) return;
    setSport(item.sport);
    setName(item.name ?? "");
    setSourceText(item.source_text ?? "");
    setScheduledTime(item.scheduled_time ?? "");
    setDurationMinutes(item.duration_minutes != null ? String(item.duration_minutes) : "");
    if (EXERCISE_SPORTS.has(item.sport) && item.steps && item.steps.length > 0) {
      setExerciseItems(apiStepsToItems(item.steps));
    } else {
      setExerciseItems([]);
    }
  }

  // A one-click "Paste copied workout" from the list view (before this form even exists) skips
  // straight to an already-filled create form, rather than opening it blank and making the
  // athlete click Paste a second time.
  useEffect(() => {
    if (pasteOnMount) handlePaste();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function insertAtCursor(text: string) {
    const el = textareaRef.current;
    if (!el) {
      setSourceText((prev) => (prev ? `${prev}\n${text}` : text));
      return;
    }
    const start = el.selectionStart ?? sourceText.length;
    const end = el.selectionEnd ?? sourceText.length;
    const before = sourceText.slice(0, start);
    const after = sourceText.slice(end);
    const needsLeadingBreak = before.length > 0 && !before.endsWith("\n");
    const insertion = `${needsLeadingBreak ? "\n" : ""}${text}\n`;
    const next = `${before}${insertion}${after}`;
    setSourceText(next);
    const cursorPos = before.length + insertion.length;
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(cursorPos, cursorPos);
    });
  }

  const preview = sport === "running" ? parseWorkoutSyntax(sourceText) : null;
  const groups = preview
    ? groupWorkoutStepsForDisplay(preview.steps.map(parsedStepToApiShape))
    : [];

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const fields = {
      sport,
      name: name.trim() || null,
      source_text: isExerciseSport ? null : sourceText.trim() || null,
      scheduled_time: scheduledTime || null,
      duration_minutes: isPlaceholderSport ? Number(durationMinutes) || null : null,
      steps: isExerciseSport ? itemsToApiSteps(exerciseItems) : null,
    };
    if (workoutId == null) {
      create.mutate({ localDate, ...fields }, { onSuccess: onDone });
    } else {
      update.mutate({ workoutId, ...fields }, { onSuccess: onDone });
    }
  }

  function handleRecurringSave() {
    recurring.mutate(
      {
        local_date: localDate,
        sport,
        name: name.trim() || null,
        source_text: isExerciseSport ? null : sourceText.trim() || null,
        scheduled_time: scheduledTime || null,
        duration_minutes: isPlaceholderSport ? Number(durationMinutes) || null : null,
        steps: isExerciseSport ? itemsToApiSteps(exerciseItems) : null,
        frequency: recurFrequency,
        interval_days:
          recurFrequency === "every_n_days" ? Number(recurIntervalDays) || undefined : undefined,
        count: recurStopMode === "count" ? Number(recurCount) || undefined : undefined,
        until: recurStopMode === "until" && recurUntil ? recurUntil : undefined,
      },
      { onSuccess: onDone },
    );
  }

  const saving = workoutId == null ? create.isPending : update.isPending;

  return (
    <form className="planned-workout-form" onSubmit={handleSave}>
      <div className="planned-workout-form__row">
        <label className="field">
          Sport
          <select className="input" value={sport} onChange={(e) => setSport(e.target.value)}>
            {SPORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Name
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={isPlaceholderSport ? "e.g. Evening yoga" : "e.g. Tempo run"}
          />
        </label>
      </div>

      {clipboardItem && (
        <button type="button" className="button" onClick={handlePaste}>
          Paste copied workout
        </button>
      )}

      {isPlaceholderSport ? (
        <>
          <div className="planned-workout-form__row">
            <label className="field">
              Duration (minutes)
              <input
                className="input"
                type="number"
                min="1"
                step="1"
                value={durationMinutes}
                onChange={(e) => setDurationMinutes(e.target.value)}
              />
            </label>
            <label className="field">
              Time of day
              <input
                className="input"
                type="time"
                value={scheduledTime}
                onChange={(e) => setScheduledTime(e.target.value)}
              />
            </label>
          </div>
          <label className="field">
            Notes (optional)
            <textarea
              className="input planned-workout-form__textarea"
              value={sourceText}
              onChange={(e) => setSourceText(e.target.value)}
              placeholder="Any detail worth remembering -- studio, route project, etc."
            />
          </label>
        </>
      ) : isExerciseSport ? (
        <>
          <label className="field">
            Time of day (optional)
            <input
              className="input"
              type="time"
              value={scheduledTime}
              onChange={(e) => setScheduledTime(e.target.value)}
            />
          </label>

          <ExerciseStepEditor items={exerciseItems} onChange={setExerciseItems} />

          {exerciseItems.length > 0 && (
            <p className="chart-note">
              Estimated duration: {Math.round(estimateItemsDurationS(exerciseItems) / 60)} min
            </p>
          )}
        </>
      ) : (
        <>
          <label className="field">
            Time of day (optional)
            <input
              className="input"
              type="time"
              value={scheduledTime}
              onChange={(e) => setScheduledTime(e.target.value)}
            />
          </label>
          <label className="field">
            Workout
            <textarea
              ref={textareaRef}
              className="input planned-workout-form__textarea"
              value={sourceText}
              onChange={(e) => setSourceText(e.target.value)}
              placeholder={"Warmup 10m\n\n4x\n3m 5:00-5:10/km Pace\n2m Z2 HR\n\nCooldown 5m"}
            />
          </label>
          <p className="chart-note">
            Add &quot;# your note&quot; at the end of a line to comment on that step.
          </p>

          <button type="button" className="button" onClick={() => setStepBuilderOpen(true)}>
            + Add step
          </button>

          {preview && preview.errors.length > 0 && (
            <ul className="planned-workout-form__errors">
              {preview.errors.map((err, i) => (
                <li key={i}>
                  Line {err.lineNo}: {err.message}
                </li>
              ))}
            </ul>
          )}

          <WorkoutStepGroups
            groups={groups}
            isExercise={false}
            footer={
              preview && (
                <p className="chart-note">
                  Estimated duration: {Math.round(preview.estimatedDurationS / 60)} min
                </p>
              )
            }
          />
        </>
      )}

      <div className="planned-workout-form__actions">
        <button type="submit" className="button button--primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" className="button" onClick={onDone}>
          Cancel
        </button>
        <button type="button" className="button" onClick={() => setShowRecurrence(!showRecurrence)}>
          Repeat this schedule…
        </button>
      </div>

      {showRecurrence && (
        <fieldset className="planned-workout-form__recurrence">
          <legend>Repeat this schedule</legend>
          <label className="field">
            Frequency
            <select
              className="input"
              value={recurFrequency}
              onChange={(e) =>
                setRecurFrequency(e.target.value as "weekly" | "every_n_days" | "monthly")
              }
            >
              <option value="weekly">Every week</option>
              <option value="every_n_days">Every N days</option>
              <option value="monthly">Every month</option>
            </select>
          </label>
          {recurFrequency === "every_n_days" && (
            <label className="field">
              N days
              <input
                className="input"
                type="number"
                min="1"
                value={recurIntervalDays}
                onChange={(e) => setRecurIntervalDays(e.target.value)}
              />
            </label>
          )}
          <div className="planned-workout-form__row">
            <label>
              <input
                type="radio"
                checked={recurStopMode === "count"}
                onChange={() => setRecurStopMode("count")}
              />
              For
            </label>
            <input
              className="input"
              type="number"
              min="2"
              disabled={recurStopMode !== "count"}
              value={recurCount}
              onChange={(e) => setRecurCount(e.target.value)}
            />
            occurrences
          </div>
          <div className="planned-workout-form__row">
            <label>
              <input
                type="radio"
                checked={recurStopMode === "until"}
                onChange={() => setRecurStopMode("until")}
              />
              Until
            </label>
            <input
              className="input"
              type="date"
              disabled={recurStopMode !== "until"}
              value={recurUntil}
              onChange={(e) => setRecurUntil(e.target.value)}
            />
          </div>
          <button
            type="button"
            className="button button--primary"
            onClick={handleRecurringSave}
            disabled={recurring.isPending}
          >
            {recurring.isPending ? "Creating…" : "Create schedule"}
          </button>
          {recurring.data && (
            <p className="chart-note">Created {recurring.data.created_dates.length} workout(s).</p>
          )}
        </fieldset>
      )}

      <StepBuilderModal
        open={stepBuilderOpen}
        onClose={() => setStepBuilderOpen(false)}
        onGenerate={insertAtCursor}
      />
    </form>
  );
}

export function ScheduleWorkoutForm({ localDate }: { localDate: string }) {
  const workouts = usePlannedWorkoutsForDate(localDate);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [addingNew, setAddingNew] = useState(false);
  const [pasteOnCreate, setPasteOnCreate] = useState(false);

  if (workouts.isLoading) return <LoadingSpinner size="sm" />;
  if (workouts.isError) return <p role="alert">Could not load the planned workouts.</p>;

  const list = (workouts.data ?? []) as ScheduledWorkout[];
  const clipboardItem = readWorkoutClipboard();

  function stopEditing() {
    setEditingId(null);
    setAddingNew(false);
    setPasteOnCreate(false);
  }

  if (addingNew) {
    return (
      <WorkoutEditForm
        localDate={localDate}
        workoutId={null}
        pasteOnMount={pasteOnCreate}
        onDone={stopEditing}
      />
    );
  }
  if (editingId != null) {
    const editing = list.find((w) => w.id === editingId);
    return (
      <WorkoutEditForm
        localDate={localDate}
        workoutId={editingId}
        initial={editing}
        onDone={stopEditing}
      />
    );
  }

  return (
    <>
      {list.map((workout) => (
        <div key={workout.id} className="planned-workout__entry">
          <WorkoutSummary workout={workout} />
          <button type="button" className="button" onClick={() => setEditingId(workout.id)}>
            Edit
          </button>
        </div>
      ))}
      <div className="planned-workout__actions">
        <button
          type="button"
          className="button button--primary"
          onClick={() => setAddingNew(true)}
        >
          {list.length === 0 ? "Schedule a workout" : "Add another workout"}
        </button>
        {clipboardItem && (
          <button
            type="button"
            className="button"
            onClick={() => {
              setPasteOnCreate(true);
              setAddingNew(true);
            }}
          >
            Paste copied workout
          </button>
        )}
      </div>
    </>
  );
}
