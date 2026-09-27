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

const ALL = "";

/** 10_800 -> "3:00", 5_400 -> "1:30" -- the "h:mm" a target is typed and shown in. */
export function formatTargetHM(totalSeconds: number): string {
  const totalMinutes = Math.round(totalSeconds / 60);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return `${h}:${m.toString().padStart(2, "0")}`;
}

/** "3:30" -> 12_600, "0:45" -> 2_700, "3" -> 10_800 (a bare number is whole hours). Anything
 * else -- minutes past 59, decimals, negatives, a zero total -- is null. */
export function parseTargetHM(text: string): number | null {
  const match = /^(\d+)(?::([0-5]\d))?$/.exec(text.trim());
  if (!match) return null;
  const seconds = (Number(match[1]) * 60 + Number(match[2] ?? 0)) * 60;
  return seconds > 0 ? seconds : null;
}

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
  const [target, setTarget] = useState(existing ? formatTargetHM(existing.target_duration_s) : "");
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
    const targetS = parseTargetHM(target);
    if (targetS == null) return;
    const body = {
      period_type: periodType,
      period_start: periodStart,
      sport: sport === ALL ? null : sport,
      target_duration_s: targetS,
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
        Target time (h:mm)
        <input
          className="input"
          type="text"
          placeholder="3:30"
          pattern="\d+(:[0-5]\d)?"
          title="Hours and minutes, e.g. 3:30 or 12:45 (a bare number is whole hours)"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
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
