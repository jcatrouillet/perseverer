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
import { Fragment, useState } from "react";

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
  onSaveNote,
  isSavingNote = false,
  isNoteError = false,
  isSaving,
  isError,
}: {
  splits: SplitOut[];
  onSetStatus: (splitIndex: number, result: string) => void;
  onSetGrade: (splitIndex: number, grade: number) => void;
  onAddRoute: (grade: number, result: string) => void;
  onDeleteRoute: (splitIndex: number) => void;
  /** Saves the athlete's note on a Kaya route (an empty string removes it). The note belongs to
   * the route itself, so it appears on every row of that route, in every activity. */
  onSaveNote?: (climbKayaId: string, note: string) => void;
  isSavingNote?: boolean;
  isNoteError?: boolean;
  isSaving: boolean;
  isError: boolean;
}) {
  const routes = boulderingRoutes(splits);
  // Kaya routes carry no per-route duration or heart rate, so those columns only appear when at
  // least one route has Garmin timing (a Garmin-only activity, or Garmin efforts Kaya never logged).
  const showTiming = routes.some((r) => r.source !== "kaya");
  // Only a Kaya route has an identity a note can follow; a Garmin-only activity has no Note column.
  const showNotes = onSaveNote != null && routes.some((r) => r.climbKayaId != null);
  const [editing, setEditing] = useState<{ splitIndex: number; climbKayaId: string } | null>(null);
  const [draft, setDraft] = useState("");
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
              {showNotes && <th>Note</th>}
              <th />
            </tr>
          </thead>
          <tbody>
            {routes.map((r) => (
              <Fragment key={r.splitIndex}>
                <tr
                  // The note shows on hover, on every row of the route -- also in any later session
                  // where this route is repeated.
                  title={r.note ?? undefined}
                  className={
                    r.result === "completed" ? "bouldering-routes-table__row--completed" : undefined
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
                  {showTiming && <td>{r.avgHr != null ? `${Math.round(r.avgHr)} bpm` : "—"}</td>}
                  {showNotes && (
                    <td>
                      {r.climbKayaId != null && (
                        <button
                          type="button"
                          className={
                            r.note
                              ? "bouldering-routes-table__note-btn bouldering-routes-table__note-btn--set"
                              : "bouldering-routes-table__note-btn"
                          }
                          aria-label={r.note ? "Edit note" : "Add note"}
                          disabled={isSavingNote}
                          onClick={() => {
                            setEditing({ splitIndex: r.splitIndex, climbKayaId: r.climbKayaId! });
                            setDraft(r.note ?? "");
                          }}
                        >
                          {r.note ? "Note" : "+ Note"}
                        </button>
                      )}
                    </td>
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
                {editing?.splitIndex === r.splitIndex && onSaveNote && (
                  <tr className="bouldering-routes-table__note-row">
                    <td colSpan={5 + (showTiming ? 2 : 0) + (showNotes ? 1 : 0)}>
                      <label className="field">
                        Note on this route — shown whenever you repeat it
                        <textarea
                          className="input"
                          rows={3}
                          maxLength={2000}
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          autoFocus
                        />
                      </label>
                      <div className="bouldering-routes-table__note-actions">
                        <button
                          type="button"
                          className="button button--primary"
                          disabled={isSavingNote}
                          onClick={() => {
                            onSaveNote(editing.climbKayaId, draft);
                            setEditing(null);
                          }}
                        >
                          {isSavingNote ? "Saving…" : "Save note"}
                        </button>
                        {r.note && (
                          <button
                            type="button"
                            className="button"
                            disabled={isSavingNote}
                            onClick={() => {
                              onSaveNote(editing.climbKayaId, "");
                              setEditing(null);
                            }}
                          >
                            Remove note
                          </button>
                        )}
                        <button type="button" className="button" onClick={() => setEditing(null)}>
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
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
              <td colSpan={(showTiming ? 3 : 1) + (showNotes ? 1 : 0)}>
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
      {(isError || isNoteError) && (
        <p role="alert" className="bouldering-routes__error">
          Could not save that change.
        </p>
      )}
    </section>
  );
}
