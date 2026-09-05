// The "Planned workout" section of MonthView's expanded-day card and DayViewPage
// (docs/adr/0015-scheduled-workouts.md): shows the scheduled workout + push status if one
// exists, or a "Schedule a workout" affordance if not. Three sport tiers, matching
// planned_workouts.py::save_planned_workout: running gets the full text-syntax editor + live
// preview; yoga/bouldering (PLACEHOLDER_SPORTS) are deliberately simpler placeholders -- a
// duration (minutes) + a time-of-day field, no structured syntax at all, per the user's own
// explicit scoping ("no structured text syntax needed, it's just to put placeholder for those
// sports"); hiit/strength_training (EXERCISE_SPORTS) get the real exercise picker
// (ExerciseStepEditor) -- the athlete's own choice over a simpler placeholder or a free-text
// syntax. All but "fitness" push to Garmin.
import { useEffect, useRef, useState } from "react";

import {
  useCreateRecurringPlannedWorkouts,
  useDeletePlannedWorkout,
  usePlannedWorkout,
  usePushPlannedWorkout,
  useSavePlannedWorkout,
} from "../api/queries";
import type { PlannedWorkoutOut } from "../api/types";
import {
  apiStepsToEntries,
  entriesToApiSteps,
  estimateExerciseDurationS,
  ExerciseStepEditor,
  preloadExerciseCatalog,
  type ExerciseEntry,
} from "./ExerciseStepEditor";
import { formatStepDurationLabel, groupWorkoutStepsForDisplay, plannedCadenceLabel, plannedTargetLabel } from "../workoutSteps";
import { parsedStepToApiShape, parseWorkoutSyntax } from "../workoutSyntax";
import { copyWorkoutToClipboard, readWorkoutClipboard } from "../workoutClipboard";
import { LoadingSpinner } from "./LoadingSpinner";
import { StepBuilderModal } from "./StepBuilderModal";
import "../styles/plannedWorkout.css";

const SPORTS = [
  { value: "running", label: "Running" },
  { value: "yoga", label: "Yoga" },
  { value: "bouldering", label: "Bouldering" },
  { value: "fitness", label: "Fitness" },
  { value: "hiit", label: "HIIT" },
  { value: "strength_training", label: "Strength training" },
];

// Sports with a real Garmin push path today -- "fitness" has neither structured syntax nor a
// placeholder builder yet (planned_workouts.py has no builder registered for it).
const PUSHABLE_SPORTS = new Set(["running", "yoga", "bouldering", "hiit", "strength_training"]);
// Sports with no structured syntax at all -- just a name, a duration, and a time of day. Mirrors
// planned_workouts.py::PLACEHOLDER_SPORTS exactly.
const PLACEHOLDER_SPORTS = new Set(["yoga", "bouldering"]);
// Real, named Garmin exercises picked from the catalog. Mirrors planned_workouts.py::
// EXERCISE_SPORTS exactly.
const EXERCISE_SPORTS = new Set(["hiit", "strength_training"]);

function statusLabel(status: PlannedWorkoutOut["push_status"]): string {
  if (status === "pushed") return "Pushed to Garmin";
  if (status === "push_failed") return "Push failed";
  return "Draft";
}

function formatDurationMinutes(estimatedDurationS: number | null): string | null {
  if (estimatedDurationS == null || estimatedDurationS <= 0) return null;
  return `${Math.round(estimatedDurationS / 60)} min`;
}

function WorkoutSummary({ localDate, workout }: { localDate: string; workout: PlannedWorkoutOut }) {
  const del = useDeletePlannedWorkout();
  const push = usePushPlannedWorkout();
  const duration = formatDurationMinutes(workout.estimated_duration_s);
  const [copied, setCopied] = useState(false);

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
        <strong>{workout.name || workout.sport}</strong>
        <span className={`planned-workout__status planned-workout__status--${workout.push_status}`}>
          {statusLabel(workout.push_status)}
        </span>
      </div>
      {(workout.scheduled_time || duration) && (
        <p className="chart-note">
          {workout.scheduled_time}
          {workout.scheduled_time && duration && " · "}
          {duration}
        </p>
      )}
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
            onClick={() => push.mutate(localDate)}
            disabled={push.isPending}
          >
            {push.isPending ? "Pushing…" : "Push to Garmin"}
          </button>
        )}
        <button type="button" className="button" onClick={handleCopy}>
          {copied ? "Copied — paste it on another day" : "Copy"}
        </button>
        <button
          type="button"
          className="button"
          onClick={() => del.mutate(localDate)}
          disabled={del.isPending}
        >
          Delete
        </button>
      </div>
    </div>
  );
}

export function ScheduleWorkoutForm({ localDate }: { localDate: string }) {
  const workout = usePlannedWorkout(localDate);
  const save = useSavePlannedWorkout();
  const recurring = useCreateRecurringPlannedWorkouts();

  // Kicks off the (code-split, ~230KB) exercise catalog fetch as soon as this day's panel
  // expands, well before the athlete might pick hiit/strength_training -- see
  // ExerciseStepEditor.tsx's own docstring for why it's dynamically imported at all.
  useEffect(() => {
    void preloadExerciseCatalog();
  }, []);

  const [editing, setEditing] = useState(false);
  const [sport, setSport] = useState("running");
  const [name, setName] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [durationMinutes, setDurationMinutes] = useState("");
  const [scheduledTime, setScheduledTime] = useState("");
  const [stepBuilderOpen, setStepBuilderOpen] = useState(false);
  const [exerciseEntries, setExerciseEntries] = useState<ExerciseEntry[]>([]);
  const [exerciseRepeatCount, setExerciseRepeatCount] = useState("");
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

  function startEditing() {
    if (workout.data?.available) {
      const sp = workout.data.sport ?? "running";
      setSport(sp);
      setName(workout.data.name ?? "");
      setSourceText(workout.data.source_text ?? "");
      setScheduledTime(workout.data.scheduled_time ?? "");
      setDurationMinutes(
        workout.data.estimated_duration_s
          ? String(Math.round(workout.data.estimated_duration_s / 60))
          : "",
      );
      if (EXERCISE_SPORTS.has(sp)) {
        const { entries, repeatCount } = apiStepsToEntries(workout.data.steps);
        setExerciseEntries(entries);
        setExerciseRepeatCount(repeatCount);
      } else {
        setExerciseEntries([]);
        setExerciseRepeatCount("");
      }
    } else {
      setSport("running");
      setName("");
      setSourceText("");
      setScheduledTime("");
      setDurationMinutes("");
      setExerciseEntries([]);
      setExerciseRepeatCount("");
    }
    setEditing(true);
  }

  function handlePaste() {
    const item = readWorkoutClipboard();
    if (!item) return;
    setSport(item.sport);
    setName(item.name ?? "");
    setSourceText(item.source_text ?? "");
    setScheduledTime(item.scheduled_time ?? "");
    setDurationMinutes(item.duration_minutes != null ? String(item.duration_minutes) : "");
    if (EXERCISE_SPORTS.has(item.sport) && item.steps && item.steps.length > 0) {
      const { entries, repeatCount } = apiStepsToEntries(item.steps);
      setExerciseEntries(entries);
      setExerciseRepeatCount(repeatCount);
    } else {
      setExerciseEntries([]);
      setExerciseRepeatCount("");
    }
  }

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
    save.mutate(
      {
        localDate,
        sport,
        name: name.trim() || null,
        source_text: isExerciseSport ? null : sourceText.trim() || null,
        scheduled_time: scheduledTime || null,
        duration_minutes: isPlaceholderSport ? Number(durationMinutes) || null : null,
        steps: isExerciseSport ? entriesToApiSteps(exerciseEntries, exerciseRepeatCount) : null,
      },
      { onSuccess: () => setEditing(false) },
    );
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
        steps: isExerciseSport ? entriesToApiSteps(exerciseEntries, exerciseRepeatCount) : null,
        frequency: recurFrequency,
        interval_days:
          recurFrequency === "every_n_days" ? Number(recurIntervalDays) || undefined : undefined,
        count: recurStopMode === "count" ? Number(recurCount) || undefined : undefined,
        until: recurStopMode === "until" && recurUntil ? recurUntil : undefined,
      },
      { onSuccess: () => setEditing(false) },
    );
  }

  if (workout.isLoading) return <LoadingSpinner size="sm" />;
  if (workout.isError) return <p role="alert">Could not load the planned workout.</p>;

  if (!editing) {
    if (workout.data?.available) {
      return (
        <>
          <WorkoutSummary localDate={localDate} workout={workout.data} />
          <button type="button" className="button" onClick={startEditing}>
            Edit
          </button>
        </>
      );
    }
    return (
      <div className="planned-workout__actions">
        <button type="button" className="button button--primary" onClick={startEditing}>
          Schedule a workout
        </button>
        {clipboardItem && (
          <button
            type="button"
            className="button"
            onClick={() => {
              handlePaste();
              setEditing(true);
            }}
          >
            Paste copied workout
          </button>
        )}
      </div>
    );
  }

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

          <ExerciseStepEditor
            entries={exerciseEntries}
            onChange={setExerciseEntries}
            repeatCount={exerciseRepeatCount}
            onRepeatCountChange={setExerciseRepeatCount}
          />

          {exerciseEntries.length > 0 && (
            <p className="chart-note">
              Estimated duration:{" "}
              {Math.round(estimateExerciseDurationS(exerciseEntries, exerciseRepeatCount) / 60)}{" "}
              min
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

          {groups.length > 0 && (
            <div className="planned-workout-form__preview">
              {groups.map((g, i) => (
                <div key={i} className="planned-workout-form__preview-group">
                  <strong>{g.label}</strong>
                  <ul>
                    {g.steps.map((s, j) => (
                      <li key={j}>
                        {formatStepDurationLabel(s)}
                        {plannedTargetLabel(s) ? ` @ ${plannedTargetLabel(s)}` : ""}
                        {plannedCadenceLabel(s) ? ` · ${plannedCadenceLabel(s)}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
              {preview && (
                <p className="chart-note">
                  Estimated duration: {Math.round(preview.estimatedDurationS / 60)} min
                </p>
              )}
            </div>
          )}
        </>
      )}

      <div className="planned-workout-form__actions">
        <button type="submit" className="button button--primary" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </button>
        <button type="button" className="button" onClick={() => setEditing(false)}>
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
            <p className="chart-note">
              Created {recurring.data.created_dates.length} workout(s)
              {recurring.data.skipped_dates.length > 0 &&
                `, skipped ${recurring.data.skipped_dates.length} already-scheduled date(s)`}
              .
            </p>
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
