// Set or edit one duration goal inside the goals popup: how much TIME, on which sport (any sport
// at all -- every activity has a duration) or on every sport combined. A brand-new weekly goal can
// also be repeated over several consecutive weeks.
import { useState } from "react";

import {
  useCreateDurationGoal,
  useRepeatDurationGoal,
  useUpdateDurationGoal,
} from "../api/queries";
import type { DurationGoalOut } from "../api/types";
import { KNOWN_SPORTS } from "../metricStyle";

const HOUR = 3600;
const ALL = "";

export function DurationGoalForm({
  periodType,
  periodStart,
  existing,
  onSaved,
  onCancel,
}: {
  periodType: "week" | "month" | "year";
  periodStart: string;
  existing: DurationGoalOut | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [sport, setSport] = useState(existing?.sport ?? ALL);
  const [hours, setHours] = useState(
    existing ? String(Math.round((existing.target_duration_s / HOUR) * 100) / 100) : "",
  );
  const create = useCreateDurationGoal();
  const update = useUpdateDurationGoal();
  const repeat = useRepeatDurationGoal();
  // Only a NEW weekly goal can be repeated across several weeks; editing changes just this one.
  const canRepeat = periodType === "week" && !existing;
  const [weeks, setWeeks] = useState("1");
  const pending = create.isPending || update.isPending || repeat.isPending;
  const error = create.error ?? update.error ?? repeat.error;

  // A goal saved for a sport that is not in the catalog stays selectable when edited.
  const sportOptions =
    existing?.sport && !KNOWN_SPORTS.includes(existing.sport)
      ? [...KNOWN_SPORTS, existing.sport]
      : KNOWN_SPORTS;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const h = Number(hours);
    if (!Number.isFinite(h) || h <= 0) return;
    const body = {
      period_type: periodType,
      period_start: periodStart,
      sport: sport === ALL ? null : sport,
      target_duration_s: Math.round(h * HOUR),
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
        Target time (hours)
        <input
          className="input"
          type="number"
          min="0.25"
          step="0.25"
          value={hours}
          onChange={(e) => setHours(e.target.value)}
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
            title="Sets this goal for that many consecutive weeks; weeks that already have it are skipped"
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
            ? "You already have a time goal for this period and sport — edit it instead."
            : "Couldn't save that goal."}
        </span>
      )}
    </form>
  );
}
