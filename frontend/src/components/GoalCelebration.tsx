// A congratulations popup, shown once per goal, when a monthly or yearly goal (distance, time or
// bouldering) of the current month or year has been reached. Mounted once in the app shell; not
// shown while a coach views an athlete (the athlete should see their own congratulations).
import { useState } from "react";

import { useBoulderingGoals, useDurationGoals, useGoalProgress } from "../api/queries";
import { localIsoDate } from "../dateUtils";
import { useDistanceFormat } from "../formatDistance";
import {
  loadCelebrated,
  reachedGoals,
  saveCelebrated,
  unseen,
  type ReachedGoal,
} from "../goalCelebration";
import { Modal } from "./Modal";
import "../styles/goal-celebration.css";

const NONE: never[] = [];

export function GoalCelebration() {
  const today = localIsoDate();
  const month = today.slice(0, 7);
  const year = today.slice(0, 4);
  const { metersToDisplay, unitLabel } = useDistanceFormat();
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  const monthDistance = useGoalProgress("month", month);
  const yearDistance = useGoalProgress("year", year);
  const monthDuration = useDurationGoals("month", month);
  const yearDuration = useDurationGoals("year", year);
  const monthBouldering = useBoulderingGoals("month", month);
  const yearBouldering = useBoulderingGoals("year", year);

  const formatDistance = (m: number) => `${metersToDisplay(m).toFixed(0)} ${unitLabel}`;
  const reached: ReachedGoal[] = [
    ...reachedGoals(
      "month",
      monthDistance.data ?? NONE,
      monthDuration.data ?? NONE,
      monthBouldering.data ?? NONE,
      formatDistance,
    ),
    ...reachedGoals(
      "year",
      yearDistance.data ?? NONE,
      yearDuration.data ?? NONE,
      yearBouldering.data ?? NONE,
      formatDistance,
    ),
  ];
  const pending = unseen(reached, loadCelebrated()).filter((g) => !dismissed.has(g.key));
  if (pending.length === 0) return null;

  const close = () => {
    saveCelebrated([...loadCelebrated(), ...pending.map((g) => g.key)]);
    setDismissed(new Set([...dismissed, ...pending.map((g) => g.key)]));
  };
  const yearly = pending.some((g) => g.period === "year");

  return (
    <Modal open onClose={close} title="Congratulations" panelClassName="goal-celebration">
      <p className="goal-celebration__lead">
        {yearly ? "You reached a yearly goal." : "You reached a monthly goal."} Well done.
      </p>
      <ul className="goal-celebration__list">
        {pending.map((g) => (
          <li key={g.key}>
            <strong>{g.title}</strong>
            <span>{g.period === "year" ? "this year" : "this month"}</span>
          </li>
        ))}
      </ul>
      <div className="goal-celebration__actions">
        <button type="button" className="button button--primary" onClick={close}>
          Thanks
        </button>
      </div>
    </Modal>
  );
}
