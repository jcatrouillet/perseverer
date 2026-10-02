// The per-route grade/status table for a bouldering activity -- reads ActivityDetail.splits
// (the raw FIT split_mesgs data, otherwise unused anywhere in the frontend) through
// boulderingRoutes.ts's reverse-engineered climb_grade/climb_result fields. See that module's
// own header comment for how the encoding was cracked.
//
// Also the editing surface for two athlete-driven corrections (see bouldering_overrides.py's own
// docstring for why both are durable, rebuild-safe records rather than one-off mutations):
// overriding a route's own status when it was logged wrong, and adding a route the device never
// tracked at all. Purely presentational, like ActivitySportCorrection/ActivityNameCorrection --
// the caller (ActivityDetailPage) owns the actual mutations and passes bound callbacks +
// pending/error state down, rather than this component reaching for React Query itself.
// Status editing is a plain always-visible <select> per row (not a "Fix it" click-to-reveal --
// a table with several routes to correct at once would make a reveal-per-row tedious), and "add
// a route" is a trailing form row rather than a modal, matching this table's own dense,
// single-surface feel.
import { useState } from "react";

import type { SplitOut } from "../api/types";
import {
  boulderingRoutes,
  formatGrade,
  formatResult,
  summarizeBoulderingRoutes,
} from "../boulderingRoutes";
import { formatClockDuration } from "../runningStats";

const KNOWN_RESULTS = ["attempt", "completed"] as const;
// A generous upper bound, not a real observed ceiling -- V0-V9 covers this athlete's real data
// (V0-V4) with plenty of headroom, matching climb_grade's own "no fixed enum" philosophy
// elsewhere in this project (additive schema evolution).
const GRADE_OPTIONS = Array.from({ length: 10 }, (_, i) => i);

export function BoulderingRoutesTable({
  splits,
  onSetStatus,
  onSetGrade,
  onAddRoute,
  onDeleteRoute,
  isSaving,
  isError,
}: {
  splits: SplitOut[];
  onSetStatus: (splitIndex: number, result: string) => void;
  onSetGrade: (splitIndex: number, grade: number) => void;
  onAddRoute: (grade: number, result: string) => void;
  onDeleteRoute: (splitIndex: number) => void;
  isSaving: boolean;
  isError: boolean;
}) {
  const routes = boulderingRoutes(splits);
  // Kaya routes carry no per-route duration or heart rate, so those columns only appear when at
  // least one route has Garmin timing (a Garmin-only activity, or Garmin efforts Kaya never logged).
  const showTiming = routes.some((r) => r.source !== "kaya");
  const [newGrade, setNewGrade] = useState(0);
  const [newResult, setNewResult] = useState<string>("completed");

  const { totalRoutes, completedRoutes } = summarizeBoulderingRoutes(routes);

  return (
    <section className="card bouldering-routes-section">
      <h2>Routes</h2>
      {totalRoutes > 0 && (
        <p className="bouldering-routes__caption">
          {completedRoutes} of {totalRoutes} route{totalRoutes === 1 ? "" : "s"} completed.
        </p>
      )}
      <div className="table-scroll">
        <table className="bouldering-routes-table">
          <thead>
            <tr>
              <th>Route</th>
              <th>Name</th>
              <th>Grade</th>
              <th>Status</th>
              {showTiming && <th>Duration</th>}
              {showTiming && <th>Avg HR</th>}
              <th />
            </tr>
          </thead>
          <tbody>
            {routes.map((r) => (
              <tr
                key={r.splitIndex}
                className={
                  r.result === "completed"
                    ? "bouldering-routes-table__row--completed"
                    : undefined
                }
              >
                <td>{r.routeNumber}</td>
                <td>{r.name ?? "—"}</td>
                <td>
                  <select
                    value={r.grade ?? ""}
                    disabled={isSaving}
                    onChange={(e) => onSetGrade(r.splitIndex, Number(e.target.value))}
                  >
                    {(r.grade == null || !GRADE_OPTIONS.includes(r.grade)) && (
                      <option value={r.grade ?? ""} disabled>
                        {formatGrade(r.grade)}
                      </option>
                    )}
                    {GRADE_OPTIONS.map((g) => (
                      <option key={g} value={g}>
                        {formatGrade(g)}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    value={r.result}
                    disabled={isSaving}
                    onChange={(e) => onSetStatus(r.splitIndex, e.target.value)}
                  >
                    {!KNOWN_RESULTS.includes(r.result as (typeof KNOWN_RESULTS)[number]) && (
                      <option value={r.result} disabled>
                        {formatResult(r.result)}
                      </option>
                    )}
                    <option value="attempt">Attempt</option>
                    <option value="completed">Completed</option>
                  </select>
                </td>
                {showTiming && (
                  <td>{r.durationS != null ? formatClockDuration(r.durationS) : "—"}</td>
                )}
                {showTiming && (
                  <td>{r.avgHr != null ? `${Math.round(r.avgHr)} bpm` : "—"}</td>
                )}
                <td>
                  {r.isManual && (
                    <button
                      type="button"
                      className="bouldering-routes-table__delete-btn"
                      title="Remove this route"
                      aria-label="Remove this route"
                      disabled={isSaving}
                      onClick={() => onDeleteRoute(r.splitIndex)}
                    >
                      ×
                    </button>
                  )}
                </td>
              </tr>
            ))}
            <tr className="bouldering-routes-table__add-row">
              <td>+</td>
              <td />
              <td>
                <select value={newGrade} onChange={(e) => setNewGrade(Number(e.target.value))}>
                  {GRADE_OPTIONS.map((g) => (
                    <option key={g} value={g}>
                      {formatGrade(g)}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <select value={newResult} onChange={(e) => setNewResult(e.target.value)}>
                  <option value="attempt">Attempt</option>
                  <option value="completed">Completed</option>
                </select>
              </td>
              <td colSpan={showTiming ? 3 : 1}>
                <button
                  type="button"
                  disabled={isSaving}
                  onClick={() => onAddRoute(newGrade, newResult)}
                >
                  {isSaving ? "Saving…" : "Add route"}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      {isError && (
        <p role="alert" className="bouldering-routes__error">
          Could not save that change.
        </p>
      )}
    </section>
  );
}
