// The header button MonthView/YearView render next to their <h1> -- the only place a goal is
// ever visible on those pages. The graph itself is deliberately never inline (per the feature's
// own requirement): it only exists inside the popup this button opens.
import { useState } from "react";

import { useDeleteGoal, useGoalProgress } from "../api/queries";
import { GoalForm } from "./GoalForm";
import { GoalProgressChart } from "./GoalProgressChart";
import { Modal } from "./Modal";
import "../styles/goals.css";

export function GoalButton({
  periodType,
  periodStart,
  periodLabel,
}: {
  periodType: "year" | "month";
  periodStart: string;
  periodLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const progress = useGoalProgress(periodType, periodStart);
  const deleteGoal = useDeleteGoal();

  const pct = progress.data?.pct_complete != null ? Math.round(progress.data.pct_complete * 100) : null;
  const buttonLabel = progress.data?.available ? `Goal: ${pct}%` : "Set goal";

  return (
    <>
      <button type="button" className="button goal-button" onClick={() => setOpen(true)}>
        {buttonLabel}
      </button>

      <Modal open={open} onClose={() => setOpen(false)} title={`${periodLabel} goal`}>
        {progress.isLoading && <p>Loading…</p>}
        {progress.isError && <p role="alert">Could not load this goal.</p>}

        {progress.data && (!progress.data.available || editing) && (
          <GoalForm
            periodType={periodType}
            periodStart={periodStart}
            existing={progress.data.goal}
            onSaved={() => setEditing(false)}
          />
        )}

        {progress.data?.available && progress.data.goal && !editing && (
          <>
            <div className="goal-progress__summary">
              <div className="goal-progress__summary-tile">
                <span className="goal-progress__summary-value">
                  {((progress.data.current_distance_m ?? 0) / 1000).toFixed(1)} km
                </span>
                <span className="goal-progress__summary-label">
                  of {(progress.data.goal.target_distance_m / 1000).toFixed(0)} km
                  {progress.data.goal.sport ? ` (${progress.data.goal.sport.replace(/_/g, " ")})` : ""}
                </span>
              </div>
              <div className="goal-progress__summary-tile">
                <span
                  className={`goal-progress__summary-value${
                    (progress.data.ahead_behind_m ?? 0) >= 0
                      ? " goal-progress__summary-value--ahead"
                      : " goal-progress__summary-value--behind"
                  }`}
                >
                  {(progress.data.ahead_behind_m ?? 0) >= 0 ? "+" : ""}
                  {((progress.data.ahead_behind_m ?? 0) / 1000).toFixed(1)} km
                </span>
                <span className="goal-progress__summary-label">
                  {(progress.data.ahead_behind_m ?? 0) >= 0 ? "ahead of" : "behind"} pace
                </span>
              </div>
              <div className="goal-progress__summary-actions">
                <button type="button" className="button" onClick={() => setEditing(true)}>
                  Edit goal
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    if (progress.data?.goal) deleteGoal.mutate(progress.data.goal);
                  }}
                >
                  Delete goal
                </button>
              </div>
            </div>
            <GoalProgressChart progress={progress.data} />
          </>
        )}
      </Modal>
    </>
  );
}
