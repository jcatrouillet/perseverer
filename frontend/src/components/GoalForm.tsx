// The small set/edit form shown inside the Goals popup -- either in place of the chart (no
// goal set yet) or revealed above it (editing an existing one). Target is entered in the
// athlete's own Personalize distance unit (km or miles) and converted to metres at submit time,
// matching AGENTS.md's SI-in-storage rule.
import { useState } from "react";

import type { GoalOut } from "../api/types";
import { useRepeatGoal, useSetGoal } from "../api/queries";
import { useDistanceFormat } from "../formatDistance";
import { DISTANCE_SPORTS } from "../metricStyle";

export function GoalForm({
  periodType,
  periodStart,
  existing,
  onSaved,
}: {
  periodType: "week" | "month" | "year";
  periodStart: string;
  existing: GoalOut | null;
  onSaved: () => void;
}) {
  const { unitLabel, metersToDisplay, displayToMeters } = useDistanceFormat();
  const [targetDisplay, setTargetDisplay] = useState(
    existing ? String(Math.round(metersToDisplay(existing.target_distance_m))) : "",
  );
  const [sport, setSport] = useState(existing?.sport ?? "running");
  // A goal saved before this list was narrowed may be scoped to a sport that is no longer offered;
  // keep it selectable so opening the edit form doesn't silently change it.
  const sportOptions =
    existing?.sport && !DISTANCE_SPORTS.includes(existing.sport)
      ? [...DISTANCE_SPORTS, existing.sport]
      : DISTANCE_SPORTS;
  const setGoal = useSetGoal();
  const repeatGoal = useRepeatGoal();
  // Only a NEW weekly goal can be repeated across several weeks; editing changes just this week.
  const canRepeat = periodType === "week" && !existing;
  const [weeks, setWeeks] = useState("1");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const target = Number(targetDisplay);
    if (!Number.isFinite(target) || target <= 0) return;
    const body = {
      period_type: periodType,
      period_start: periodStart,
      sport: sport === "" ? null : sport,
      target_distance_m: displayToMeters(target),
    };
    const repeatCount = Number(weeks);
    if (canRepeat && Number.isInteger(repeatCount) && repeatCount > 1) {
      repeatGoal.mutate({ ...body, weeks: repeatCount }, { onSuccess: onSaved });
    } else {
      setGoal.mutate(body, { onSuccess: onSaved });
    }
  };

  return (
    <form className="goal-form" onSubmit={handleSubmit}>
      <label className="field">
        Sport
        <select className="input" value={sport} onChange={(e) => setSport(e.target.value)}>
          <option value="">All sports</option>
          {sportOptions.map((s) => (
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
      {canRepeat && (
        <label className="field">
          Repeat for (weeks)
          <input
            className="input"
            type="number"
            min="1"
            max="104"
            step="1"
            title="Sets this goal for that many consecutive weeks, replacing any goal already set in those weeks"
            value={weeks}
            onChange={(e) => setWeeks(e.target.value)}
          />
        </label>
      )}
      <button
        type="submit"
        className="button button--primary"
        disabled={setGoal.isPending || repeatGoal.isPending}
      >
        {existing ? "Save" : "Set goal"}
      </button>
      {(setGoal.isError || repeatGoal.isError) && (
        <span role="alert">Couldn't save that goal.</span>
      )}
    </form>
  );
}
