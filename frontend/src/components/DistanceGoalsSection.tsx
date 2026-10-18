// The distance half of the goals popup GoalButton.tsx opens: every distance goal set for that
// week/month/year -- one per sport, plus optionally one for every sport combined -- each with its
// progress against a straight-line pace, and a form to add more. Same shape as the time goals
// beside it (DurationGoalsSection.tsx).
import { useState } from "react";

import { useDeleteGoal, useGoalProgress } from "../api/queries";
import type { GoalOut, GoalProgressOut } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import { GoalForm } from "./GoalForm";
import { GoalProgressChart } from "./GoalProgressChart";
import { LoadingSpinner } from "./LoadingSpinner";
import "../styles/goals.css";

/** "500 km running", "1,000 km all sports". */
function useGoalTitle() {
  const { metersToDisplay, unitLabel } = useDistanceFormat();
  return (goal: GoalOut) => {
    const sport = goal.sport == null ? "all sports" : goal.sport.replace(/_/g, " ");
    return `${metersToDisplay(goal.target_distance_m).toFixed(0)} ${unitLabel} ${sport}`;
  };
}

function GoalCard({ progress, onEdit }: { progress: GoalProgressOut; onEdit: () => void }) {
  const { metersToDisplay, unitLabel } = useDistanceFormat();
  const title = useGoalTitle();
  const deleteGoal = useDeleteGoal();
  const goal = progress.goal;
  if (goal == null) return null;
  const aheadBehind = progress.ahead_behind_m ?? 0;
  const ahead = aheadBehind >= 0;
  return (
    <section className="bouldering-goal">
      <h4 className="bouldering-goal__title">{title(goal)}</h4>
      <div className="goal-progress__summary">
        <div className="goal-progress__summary-tile">
          <span className="goal-progress__summary-value">
            {metersToDisplay(progress.current_distance_m ?? 0).toFixed(1)} {unitLabel}
          </span>
          <span className="goal-progress__summary-label">
            of {metersToDisplay(goal.target_distance_m).toFixed(0)} {unitLabel} (
            {Math.round((progress.pct_complete ?? 0) * 100)}%)
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
            {ahead ? "+" : ""}
            {metersToDisplay(aheadBehind).toFixed(1)} {unitLabel}
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
            onClick={() => deleteGoal.mutate(goal)}
          >
            Delete
          </button>
        </div>
      </div>
      <GoalProgressChart progress={progress} />
    </section>
  );
}

export function DistanceGoalsSection({
  periodType,
  periodStart,
}: {
  periodType: "week" | "month" | "year";
  periodStart: string;
}) {
  const [editing, setEditing] = useState<GoalOut | "new" | null>(null);
  const goals = useGoalProgress(periodType, periodStart);

  return (
    <section className="distance-goal bouldering-goals">
      <h3 className="bouldering-goals__heading">Distance</h3>
      {goals.isLoading && <LoadingSpinner size="sm" />}
      {goals.isError && <p role="alert">Could not load the distance goals.</p>}

      {goals.data && goals.data.length === 0 && editing === null && (
        <p className="chart-note">
          No distance goal for this period yet. Set a target distance for any sport that covers one,
          or for all of them together.
        </p>
      )}

      {goals.data?.map((p) =>
        p.goal == null ? null : editing !== "new" && editing?.id === p.goal.id ? (
          <GoalForm
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
        <GoalForm
          periodType={periodType}
          periodStart={periodStart}
          existing={null}
          onSaved={() => setEditing(null)}
          onCancel={() => setEditing(null)}
        />
      ) : (
        goals.data && (
          <button type="button" className="button" onClick={() => setEditing("new")}>
            + Add a distance goal
          </button>
        )
      )}
    </section>
  );
}
