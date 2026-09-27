// Set or edit one bouldering goal inside the goals popup: how many routes, at which V-grade (or
// any grade), and whether harder routes count too. Counting is always COMPLETED routes only.
import { useState } from "react";

import { useCreateBoulderingGoal, useUpdateBoulderingGoal } from "../api/queries";
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
  const pending = create.isPending || update.isPending;
  const error = create.error ?? update.error;

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
    if (existing) update.mutate({ id: existing.id, ...body }, { onSuccess: onSaved });
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
