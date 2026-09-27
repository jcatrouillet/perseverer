// Set or edit one bouldering goal inside the goals popup: how many routes, at which V-grade (or
// any grade), and whether harder routes count too. Counting is always COMPLETED routes only.
import { useState } from "react";

import {
  useCreateBoulderingGoal,
  useRepeatBoulderingGoal,
  useUpdateBoulderingGoal,
} from "../api/queries";
import type { BoulderingGoalOut } from "../api/types";
import { formatGrade } from "../boulderingRoutes";

const GRADES = Array.from({ length: 18 }, (_, i) => i); // V0..V17
const ANY = "any";

export function BoulderingGoalForm({
  periodType,
  periodStart,
  existing,
  onSaved,
  onCancel,
}: {
  periodType: "week" | "month" | "year";
  periodStart: string;
  existing: BoulderingGoalOut | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [grade, setGrade] = useState(existing?.grade != null ? String(existing.grade) : ANY);
  const [andHarder, setAndHarder] = useState(existing?.and_harder ?? false);
  const [target, setTarget] = useState(existing ? String(existing.target_count) : "");
  const create = useCreateBoulderingGoal();
  const update = useUpdateBoulderingGoal();
  const repeat = useRepeatBoulderingGoal();
  // Only a NEW weekly goal can be repeated across several weeks; editing changes just this one.
  const canRepeat = periodType === "week" && !existing;
  const [weeks, setWeeks] = useState("1");
  const pending = create.isPending || update.isPending || repeat.isPending;
  const error = create.error ?? update.error ?? repeat.error;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const count = Number(target);
    if (!Number.isInteger(count) || count <= 0) return;
    const body = {
      period_type: periodType,
      period_start: periodStart,
      grade: grade === ANY ? null : Number(grade),
      and_harder: grade === ANY ? false : andHarder,
      target_count: count,
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
        Grade
        <select className="input" value={grade} onChange={(e) => setGrade(e.target.value)}>
          <option value={ANY}>Any grade</option>
          {GRADES.map((g) => (
            <option key={g} value={g}>
              {formatGrade(g)}
            </option>
          ))}
        </select>
      </label>
      <label className="field goal-form__check">
        <input
          type="checkbox"
          checked={andHarder}
          disabled={grade === ANY}
          onChange={(e) => setAndHarder(e.target.checked)}
        />
        or harder
      </label>
      <label className="field">
        Completed routes
        <input
          className="input"
          type="number"
          min="1"
          step="1"
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
            value={weeks}
            onChange={(e) => setWeeks(e.target.value)}
          />
          <span className="field__hint">
            Sets this same goal for that many consecutive weeks, starting with this one. A week that
            already has it is left as it is.
          </span>
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
            ? "You already have a goal for this period and grade — edit it instead."
            : "Couldn't save that goal."}
        </span>
      )}
    </form>
  );
}
