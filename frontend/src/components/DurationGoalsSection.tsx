// The time half of the goals popup GoalButton.tsx opens: every duration goal set for that week/
// month/year -- one per sport, plus optionally one for every sport combined -- each with its
// progress against a straight-line pace, and a form to add more. Works for any sport.
import { useState } from "react";

import { useDeleteDurationGoal, useDurationGoals } from "../api/queries";
import type { DurationGoalOut, DurationGoalProgressOut } from "../api/types";
import { formatDurationHM } from "../runningStats";
import { DurationGoalChart } from "./DurationGoalChart";
import { DurationGoalForm } from "./DurationGoalForm";
import { LoadingSpinner } from "./LoadingSpinner";
import "../styles/goals.css";

/** "3h yoga", "10h all sports". */
export function durationGoalTitle(goal: DurationGoalOut): string {
  const sport = goal.sport == null ? "all sports" : goal.sport.replace(/_/g, " ");
  return `${formatDurationHM(goal.target_duration_s)} ${sport}`;
}

function GoalCard({ progress, onEdit }: { progress: DurationGoalProgressOut; onEdit: () => void }) {
  const deleteGoal = useDeleteDurationGoal();
  const ahead = progress.ahead_behind_s >= 0;
  return (
    <section className="bouldering-goal">
      <h4 className="bouldering-goal__title">{durationGoalTitle(progress.goal)}</h4>
      <div className="goal-progress__summary">
        <div className="goal-progress__summary-tile">
          <span className="goal-progress__summary-value">
            {formatDurationHM(progress.current_duration_s)}
          </span>
          <span className="goal-progress__summary-label">
            of {formatDurationHM(progress.goal.target_duration_s)} (
            {Math.round(progress.pct_complete * 100)}%)
          </span>
        </div>
        <div className="goal-progress__summary-tile">
          <span
            className={`goal-progress__summary-value${
              ahead
                ? " goal-progress__summary-value--ahead"
                : " goal-progress__summary-value--behind"
            }`}
          >
            {ahead ? "+" : "-"}
            {formatDurationHM(Math.abs(progress.ahead_behind_s))}
          </span>
          <span className="goal-progress__summary-label">{ahead ? "ahead of" : "behind"} pace</span>
        </div>
        <div className="goal-progress__summary-actions">
          <button type="button" className="button" onClick={onEdit}>
            Edit
          </button>
          <button
            type="button"
            className="button"
            disabled={deleteGoal.isPending}
            onClick={() => deleteGoal.mutate(progress.goal)}
          >
            Delete
          </button>
        </div>
      </div>
      <DurationGoalChart progress={progress} />
    </section>
  );
}

export function DurationGoalsSection({
  periodType,
  periodStart,
}: {
  periodType: "week" | "month" | "year";
  periodStart: string;
}) {
  const [editing, setEditing] = useState<DurationGoalOut | "new" | null>(null);
  const goals = useDurationGoals(periodType, periodStart);

  return (
    <section className="bouldering-goals">
      <h3 className="bouldering-goals__heading">Time</h3>
      {goals.isLoading && <LoadingSpinner size="sm" />}
      {goals.isError && <p role="alert">Could not load the time goals.</p>}

      {goals.data && goals.data.length === 0 && editing === null && (
        <p className="chart-note">
          No time goal for this period yet. Set a target amount of time for any sport, or for all of
          them together.
        </p>
      )}

      {goals.data?.map((p) =>
        editing !== "new" && editing?.id === p.goal.id ? (
          <DurationGoalForm
            key={p.goal.id}
            periodType={periodType}
            periodStart={periodStart}
            existing={p.goal}
            onSaved={() => setEditing(null)}
            onCancel={() => setEditing(null)}
          />
        ) : (
          <GoalCard key={p.goal.id} progress={p} onEdit={() => setEditing(p.goal)} />
        ),
      )}

      {editing === "new" ? (
        <DurationGoalForm
          periodType={periodType}
          periodStart={periodStart}
          existing={null}
          onSaved={() => setEditing(null)}
          onCancel={() => setEditing(null)}
        />
      ) : (
        goals.data && (
          <button type="button" className="button" onClick={() => setEditing("new")}>
            + Add a time goal
          </button>
        )
      )}
    </section>
  );
}
