// The one "Goals" button MonthView/YearView/WeekView render next to their <h1> -- the only place a
// goal is ever visible on those pages. The popup it opens holds every kind of goal for that period:
// the distance goal (running or any sport) and any number of bouldering goals, for a week, month
// or year alike. The graphs are deliberately never inline: they only exist inside the popup.
import { useState } from "react";

import { useDeleteGoal, useGoalProgress } from "../api/queries";
import { useDistanceFormat } from "../formatDistance";
import { BoulderingGoalsSection } from "./BoulderingGoalsSection";
import { GoalForm } from "./GoalForm";
import { GoalProgressChart } from "./GoalProgressChart";
import { LoadingSpinner } from "./LoadingSpinner";
import { Modal } from "./Modal";
import "../styles/goals.css";

export function GoalButton({
  periodType,
  periodStart,
  periodLabel,
}: {
  periodType: "week" | "month" | "year";
  periodStart: string;
  periodLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const progress = useGoalProgress(periodType, periodStart);
  const deleteGoal = useDeleteGoal();
  const { metersToDisplay, unitLabel } = useDistanceFormat();

  return (
    <>
      <button type="button" className="button goal-button" onClick={() => setOpen(true)}>
        Goals
      </button>

      <Modal open={open} onClose={() => setOpen(false)} title={`${periodLabel} goals`}>
        <section className="distance-goal">
          <h3 className="bouldering-goals__heading">Running &amp; distance</h3>
          {progress.isLoading && <LoadingSpinner size="sm" />}
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
                    {metersToDisplay(progress.data.current_distance_m ?? 0).toFixed(1)} {unitLabel}
                  </span>
                  <span className="goal-progress__summary-label">
                    of {metersToDisplay(progress.data.goal.target_distance_m).toFixed(0)}{" "}
                    {unitLabel}
                    {progress.data.goal.sport
                      ? ` (${progress.data.goal.sport.replace(/_/g, " ")})`
                      : ""}
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
                    {metersToDisplay(progress.data.ahead_behind_m ?? 0).toFixed(1)} {unitLabel}
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
        </section>

        <BoulderingGoalsSection periodType={periodType} periodStart={periodStart} />
      </Modal>
    </>
  );
}
