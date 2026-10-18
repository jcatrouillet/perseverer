// The one "Goals" button MonthView/YearView/WeekView render next to their <h1> -- the only place a
// goal is ever visible on those pages. The popup it opens holds every kind of goal for that period:
// any number of distance goals and time goals (one per sport, or for all sports together) and any
// number of bouldering goals, for a week, month or year alike. The graphs are deliberately never
// inline: they only exist inside the popup.
import { useState } from "react";

import { BoulderingGoalsSection } from "./BoulderingGoalsSection";
import { DistanceGoalsSection } from "./DistanceGoalsSection";
import { DurationGoalsSection } from "./DurationGoalsSection";
import { Icon } from "./Icon";
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

  return (
    <>
      <button type="button" className="button goal-button" onClick={() => setOpen(true)}>
        <Icon name="target" />
        Goals
      </button>

      <Modal open={open} onClose={() => setOpen(false)} title={`${periodLabel} goals`}>
        <DistanceGoalsSection periodType={periodType} periodStart={periodStart} />

        <DurationGoalsSection periodType={periodType} periodStart={periodStart} />

        <BoulderingGoalsSection periodType={periodType} periodStart={periodStart} />
      </Modal>
    </>
  );
}
