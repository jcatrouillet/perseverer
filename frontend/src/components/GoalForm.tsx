// The small set/edit form shown inside the Goals popup -- either in place of the chart (no
// goal set yet) or revealed above it (editing an existing one). Target is entered in the
// athlete's own Personalize distance unit (km or miles) and converted to metres at submit time,
// matching AGENTS.md's SI-in-storage rule.
import { useState } from "react";

import type { GoalOut } from "../api/types";
import { useSetGoal } from "../api/queries";
import { useDistanceFormat } from "../formatDistance";
import { KNOWN_SPORTS } from "../metricStyle";

export function GoalForm({
  periodType,
  periodStart,
  existing,
  onSaved,
}: {
  periodType: "year" | "month";
  periodStart: string;
  existing: GoalOut | null;
  onSaved: () => void;
}) {
  const { unitLabel, metersToDisplay, displayToMeters } = useDistanceFormat();
  const [targetDisplay, setTargetDisplay] = useState(
    existing ? String(Math.round(metersToDisplay(existing.target_distance_m))) : "",
  );
  const [sport, setSport] = useState(existing?.sport ?? "running");
  const setGoal = useSetGoal();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const target = Number(targetDisplay);
    if (!Number.isFinite(target) || target <= 0) return;
    setGoal.mutate(
      {
        period_type: periodType,
        period_start: periodStart,
        sport: sport === "" ? null : sport,
        target_distance_m: displayToMeters(target),
      },
      { onSuccess: onSaved },
    );
  };

  return (
    <form className="goal-form" onSubmit={handleSubmit}>
      <label className="field">
        Sport
        <select className="input" value={sport} onChange={(e) => setSport(e.target.value)}>
          <option value="">All sports</option>
          {KNOWN_SPORTS.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Target distance ({unitLabel})
        <input
          className="input"
          type="number"
          min="1"
          step="1"
          value={targetDisplay}
          onChange={(e) => setTargetDisplay(e.target.value)}
          required
        />
      </label>
      <button type="submit" className="button button--primary" disabled={setGoal.isPending}>
        {existing ? "Save" : "Set goal"}
      </button>
      {setGoal.isError && <span role="alert">Couldn't save that goal.</span>}
    </form>
  );
}
