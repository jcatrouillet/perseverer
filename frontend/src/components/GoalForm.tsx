// Set or edit one distance goal inside the goals popup: how far, on which sport that covers a
// distance, or on every sport combined. Target is entered in the athlete's own Personalize
// distance unit (km or miles) and converted to metres at submit time, matching AGENTS.md's
// SI-in-storage rule. A brand-new weekly goal can also be repeated over several consecutive weeks.
import { useState } from "react";

import type { GoalOut } from "../api/types";
import { useCreateGoal, useRepeatGoal, useUpdateGoal } from "../api/queries";
import { useDistanceFormat } from "../formatDistance";
import { DISTANCE_SPORTS } from "../metricStyle";

const ALL = "";

export function GoalForm({
  periodType,
  periodStart,
  existing,
  onSaved,
  onCancel,
}: {
  periodType: "week" | "month" | "year";
  periodStart: string;
  existing: GoalOut | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { unitLabel, metersToDisplay, displayToMeters } = useDistanceFormat();
  const [targetDisplay, setTargetDisplay] = useState(
    existing ? String(Math.round(metersToDisplay(existing.target_distance_m))) : "",
  );
  const [sport, setSport] = useState(existing ? (existing.sport ?? ALL) : "running");
  // A goal saved for a sport that is not offered any more stays selectable when edited, so
  // opening the form doesn't silently change it.
  const sportOptions =
    existing?.sport && !DISTANCE_SPORTS.includes(existing.sport)
      ? [...DISTANCE_SPORTS, existing.sport]
      : DISTANCE_SPORTS;
  const create = useCreateGoal();
  const update = useUpdateGoal();
  const repeat = useRepeatGoal();
  // Only a NEW weekly goal can be repeated across several weeks; editing changes just this one.
  const canRepeat = periodType === "week" && !existing;
  const [weeks, setWeeks] = useState("1");
  const pending = create.isPending || update.isPending || repeat.isPending;
  const error = create.error ?? update.error ?? repeat.error;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const target = Number(targetDisplay);
    if (!Number.isFinite(target) || target <= 0) return;
    const body = {
      period_type: periodType,
      period_start: periodStart,
      sport: sport === ALL ? null : sport,
      target_distance_m: displayToMeters(target),
    };
    const repeatCount = Number(weeks);
    if (existing) update.mutate({ id: existing.id, ...body }, { onSuccess: onSaved });
    else if (canRepeat && Number.isInteger(repeatCount) && repeatCount > 1)
      repeat.mutate({ ...body, weeks: repeatCount }, { onSuccess: onSaved });
    else create.mutate(body, { onSuccess: onSaved });
  };

  return (
    <form className="goal-form" onSubmit={handleSubmit}>
      <label className="field">
        Sport
        <select className="input" value={sport} onChange={(e) => setSport(e.target.value)}>
          <option value={ALL}>All sports</option>
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
            title="Sets this goal for that many consecutive weeks, replacing a goal for the same sport already set in those weeks"
            value={weeks}
            onChange={(e) => setWeeks(e.target.value)}
          />
        </label>
      )}
      <button type="submit" className="button button--primary" disabled={pending}>
        {existing ? "Save" : "Add goal"}
      </button>
      <button type="button" className="button" onClick={onCancel}>
        Cancel
      </button>
      {error && (
        <span role="alert">
          {/409/.test(error.message)
            ? "You already have a distance goal for this period and sport — edit it instead."
            : "Couldn't save that goal."}
        </span>
      )}
    </form>
  );
}
