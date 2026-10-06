// The bouldering half of the goals popup GoalButton.tsx opens (there is one "goal" button per
// period, not a separate one per sport): every bouldering goal set for that week/month/year --
// several may share a period ("10x V4 this year" and "1x V5 in October" are two goals) -- each
// with its progress against a straight-line pace, plus a form to add more.
import { useState } from "react";

import { useBoulderingGoals, useDeleteBoulderingGoal } from "../api/queries";
import type { BoulderingGoalOut, BoulderingGoalProgressOut } from "../api/types";
import { formatGrade } from "../boulderingRoutes";
import { BoulderingGoalChart } from "./BoulderingGoalChart";
import { BoulderingGoalForm } from "./BoulderingGoalForm";
import { LoadingSpinner } from "./LoadingSpinner";
import "../styles/goals.css";

/** "10 × V4", "5 × V4 or harder", "8 × any grade". */
export function goalTitle(goal: BoulderingGoalOut): string {
  const grade =
    goal.grade == null
      ? "any grade"
      : `${formatGrade(goal.grade)}${goal.and_harder ? " or harder" : ""}`;
  return `${goal.target_count} × ${grade}`;
}

function GoalCard({
  progress,
  onEdit,
}: {
  progress: BoulderingGoalProgressOut;
  onEdit: () => void;
}) {
  const deleteGoal = useDeleteBoulderingGoal();
  const ahead = progress.ahead_behind >= 0;
  return (
    <section className="bouldering-goal">
      <h4 className="bouldering-goal__title">{goalTitle(progress.goal)}</h4>
      <div className="goal-progress__summary">
        <div className="goal-progress__summary-tile">
          <span className="goal-progress__summary-value">{progress.current_count}</span>
          <span className="goal-progress__summary-label">
            of {progress.goal.target_count} completed ({Math.round(progress.pct_complete * 100)}%)
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
            {Math.abs(progress.ahead_behind).toFixed(1)}
          </span>
          <span className="goal-progress__summary-label">
            routes {ahead ? "ahead of" : "behind"} pace
          </span>
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
      <BoulderingGoalChart progress={progress} />
    </section>
  );
}

export function BoulderingGoalsSection({
  periodType,
  periodStart,
}: {
  periodType: "week" | "month" | "year";
  periodStart: string;
}) {
  const [editing, setEditing] = useState<BoulderingGoalOut | "new" | null>(null);
  const goals = useBoulderingGoals(periodType, periodStart);

  return (
    <section className="bouldering-goals">
      <h3 className="bouldering-goals__heading">Bouldering</h3>
      {goals.isLoading && <LoadingSpinner size="sm" />}
      {goals.isError && <p role="alert">Could not load the bouldering goals.</p>}

      {goals.data && goals.data.length === 0 && editing === null && (
        <p className="chart-note">
          No bouldering goal for this period yet. A goal counts completed routes only — for example
          10 × V4 in a year, or 1 × V5 in a month.
        </p>
      )}

      {goals.data?.map((p) =>
        editing !== "new" && editing?.id === p.goal.id ? (
          <BoulderingGoalForm
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
        <BoulderingGoalForm
          periodType={periodType}
          periodStart={periodStart}
          existing={null}
          onSaved={() => setEditing(null)}
          onCancel={() => setEditing(null)}
        />
      ) : (
        goals.data && (
          <button type="button" className="button" onClick={() => setEditing("new")}>
            + Add a bouldering goal
          </button>
        )
      )}
    </section>
  );
}
