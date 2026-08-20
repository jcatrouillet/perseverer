// The small set/edit form shown inside the Goals popup -- either in place of the chart (no
// goal set yet) or revealed above it (editing an existing one). Target is entered in km (the
// unit the reference widget and every other distance readout in this app already use) and
// converted to metres at submit time, matching CLAUDE.md's SI-in-storage rule.
import { useState } from "react";

import type { GoalOut } from "../api/types";
import { useSetGoal } from "../api/queries";
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
  const [targetKm, setTargetKm] = useState(
    existing ? String(Math.round(existing.target_distance_m / 1000)) : "",
  );
  const [sport, setSport] = useState(existing?.sport ?? "running");
  const setGoal = useSetGoal();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const km = Number(targetKm);
    if (!Number.isFinite(km) || km <= 0) return;
    setGoal.mutate(
      {
        period_type: periodType,
        period_start: periodStart,
        sport: sport === "" ? null : sport,
        target_distance_m: km * 1000,
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
        Target distance (km)
        <input
          className="input"
          type="number"
          min="1"
          step="1"
          value={targetKm}
          onChange={(e) => setTargetKm(e.target.value)}
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
