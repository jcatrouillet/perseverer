// Health page: athlete-entered blood test results, grouped by draw date into "panels" -- see
// api/routers/blood_tests.py's own docstring for why this is a plain table, not the
// health_observation EAV pipeline every vendor adapter feeds (athlete-entered, not
// vendor-parsed). Reference ranges are the athlete's own, copied from their lab report --
// informational only (a simple out-of-range visual flag against the athlete's OWN stated range),
// never a canonical "normal range" this app asserts. Collapsed-by-default add form, same
// reveal-on-click convention NotesPanel.tsx already established, since a panel typically has many
// markers entered at once and the form shouldn't dominate the page while empty.
import { useMemo, useState } from "react";

import {
  useBloodTests,
  useCreateBloodTestBatch,
  useDeleteBloodTestPanel,
  useDeleteBloodTestResult,
  useUpdateBloodTestResult,
} from "../api/queries";
import type { BloodTestMarkerIn, BloodTestResultOut } from "../api/types";
import { EARLIEST_PLAUSIBLE_DATE, isoDate, parseIsoDate } from "../dateUtils";
import { LoadingSpinner } from "./LoadingSpinner";
import "../styles/blood-tests.css";

const TODAY = isoDate(new Date());

function formatDate(localDate: string): string {
  // timeZone: "UTC" is required, not decorative -- parseIsoDate returns a UTC-midnight Date, and
  // toLocaleDateString defaults to the browser's own local timezone, which silently rolls the
  // displayed date back a day for anyone west of UTC (confirmed live: entering "2026-09-13"
  // rendered back as "Sep 12, 2026" without this). Same fix RunningStats.tsx's own
  // formatShortDate already applies to the identical parse-then-format shape.
  return parseIsoDate(localDate).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

interface Panel {
  localDate: string;
  labName: string | null;
  results: BloodTestResultOut[];
}

function groupIntoPanels(results: BloodTestResultOut[]): Panel[] {
  const byDate = new Map<string, BloodTestResultOut[]>();
  for (const r of results) {
    const list = byDate.get(r.local_date) ?? [];
    list.push(r);
    byDate.set(r.local_date, list);
  }
  return [...byDate.entries()]
    .sort(([a], [b]) => (a < b ? 1 : -1))
    .map(([localDate, rows]) => ({
      localDate,
      labName: rows.find((r) => r.lab_name)?.lab_name ?? null,
      results: rows,
    }));
}

function isOutOfRange(r: BloodTestResultOut): boolean {
  return (
    (r.reference_low != null && r.value_num < r.reference_low) ||
    (r.reference_high != null && r.value_num > r.reference_high)
  );
}

function formatRange(r: BloodTestResultOut): string {
  if (r.reference_low == null && r.reference_high == null) return "—";
  if (r.reference_low != null && r.reference_high != null) {
    return `${r.reference_low}–${r.reference_high}`;
  }
  if (r.reference_low != null) return `≥ ${r.reference_low}`;
  return `≤ ${r.reference_high}`;
}

function ResultRow({ result }: { result: BloodTestResultOut }) {
  const updateResult = useUpdateBloodTestResult();
  const deleteResult = useDeleteBloodTestResult();
  const [isEditing, setIsEditing] = useState(false);
  const [value, setValue] = useState(String(result.value_num));
  const [unit, setUnit] = useState(result.unit ?? "");
  const [refLow, setRefLow] = useState(result.reference_low != null ? String(result.reference_low) : "");
  const [refHigh, setRefHigh] = useState(
    result.reference_high != null ? String(result.reference_high) : "",
  );

  function save() {
    const parsedValue = Number(value);
    if (!Number.isFinite(parsedValue)) return;
    updateResult.mutate(
      {
        resultId: result.id,
        local_date: result.local_date,
        marker: result.marker,
        value_num: parsedValue,
        unit: unit.trim() === "" ? null : unit.trim(),
        reference_low: refLow.trim() === "" ? null : Number(refLow),
        reference_high: refHigh.trim() === "" ? null : Number(refHigh),
        lab_name: result.lab_name,
        notes: result.notes,
      },
      { onSuccess: () => setIsEditing(false) },
    );
  }

  if (isEditing) {
    return (
      <tr className="blood-tests__row blood-tests__row--editing">
        <td>{result.marker}</td>
        <td>
          <input
            className="input blood-tests__cell-input"
            type="number"
            step="any"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </td>
        <td>
          <input
            className="input blood-tests__cell-input"
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
            placeholder="unit"
          />
        </td>
        <td>
          <input
            className="input blood-tests__cell-input"
            type="number"
            step="any"
            value={refLow}
            onChange={(e) => setRefLow(e.target.value)}
            placeholder="low"
          />
          {" – "}
          <input
            className="input blood-tests__cell-input"
            type="number"
            step="any"
            value={refHigh}
            onChange={(e) => setRefHigh(e.target.value)}
            placeholder="high"
          />
        </td>
        <td className="blood-tests__actions">
          <button
            type="button"
            className="button button--primary"
            disabled={updateResult.isPending}
            onClick={save}
          >
            {updateResult.isPending ? "Saving…" : "Save"}
          </button>
          <button type="button" className="button" onClick={() => setIsEditing(false)}>
            Cancel
          </button>
        </td>
      </tr>
    );
  }

  return (
    <tr className={isOutOfRange(result) ? "blood-tests__row blood-tests__row--out-of-range" : "blood-tests__row"}>
      <td>{result.marker}</td>
      <td>
        {result.value_num}
        {isOutOfRange(result) && (
          <span className="badge blood-tests__flag" title="Outside the reference range provided">
            Out of range
          </span>
        )}
      </td>
      <td>{result.unit ?? "—"}</td>
      <td>{formatRange(result)}</td>
      <td className="blood-tests__actions">
        <button type="button" className="button" onClick={() => setIsEditing(true)}>
          Edit
        </button>
        <button
          type="button"
          className="button"
          disabled={deleteResult.isPending}
          onClick={() => deleteResult.mutate(result.id)}
        >
          Delete
        </button>
      </td>
    </tr>
  );
}

function PanelMeta({ panel }: { panel: Panel }) {
  // Lab name/notes are shared across every marker in a panel (see api/routers/blood_tests.py's
  // own docstring -- one draw date, one lab_name/notes, several markers), but the backend still
  // stores them per-row rather than once per panel. Editing here writes the same lab_name/notes
  // to every result in the panel in one go, rather than exposing them as N separately-editable
  // per-marker fields that could silently drift apart from each other.
  const updateResult = useUpdateBloodTestResult();
  const [isEditing, setIsEditing] = useState(false);
  const [labName, setLabName] = useState(panel.labName ?? "");
  const [notes, setNotes] = useState(panel.results[0]?.notes ?? "");
  const [isSaving, setIsSaving] = useState(false);

  function startEditing() {
    setLabName(panel.labName ?? "");
    setNotes(panel.results[0]?.notes ?? "");
    setIsEditing(true);
  }

  async function save() {
    setIsSaving(true);
    const trimmedLab = labName.trim() === "" ? null : labName.trim();
    const trimmedNotes = notes.trim() === "" ? null : notes.trim();
    try {
      await Promise.all(
        panel.results.map((r) =>
          updateResult.mutateAsync({
            resultId: r.id,
            local_date: r.local_date,
            marker: r.marker,
            value_num: r.value_num,
            unit: r.unit,
            reference_low: r.reference_low,
            reference_high: r.reference_high,
            lab_name: trimmedLab,
            notes: trimmedNotes,
          }),
        ),
      );
      setIsEditing(false);
    } finally {
      setIsSaving(false);
    }
  }

  if (isEditing) {
    return (
      <div className="blood-tests__form">
        <label>
          Lab (optional)
          <input
            className="input"
            value={labName}
            onChange={(e) => setLabName(e.target.value)}
            placeholder="e.g. Quest Diagnostics"
          />
        </label>
        <label>
          Notes (optional)
          <textarea
            className="input"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. Fasting draw"
          />
        </label>
        <div className="blood-tests__form-actions">
          <button type="button" className="button button--primary" disabled={isSaving} onClick={save}>
            {isSaving ? "Saving…" : "Save"}
          </button>
          <button type="button" className="button" onClick={() => setIsEditing(false)}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="blood-tests__meta">
      {panel.results[0]?.notes && <p className="chart-note">{panel.results[0].notes}</p>}
      <button type="button" className="button" onClick={startEditing}>
        Edit lab / notes
      </button>
    </div>
  );
}

function PanelCard({ panel, defaultOpen }: { panel: Panel; defaultOpen: boolean }) {
  const deletePanel = useDeleteBloodTestPanel();

  return (
    <details className="blood-tests__panel" open={defaultOpen}>
      <summary className="blood-tests__summary">
        <span className="blood-tests__summary-date">{formatDate(panel.localDate)}</span>
        {panel.labName && <span className="blood-tests__summary-lab">{panel.labName}</span>}
        <span className="chart-note">{panel.results.length} marker(s)</span>
      </summary>
      <div className="blood-tests__panel-body">
        <PanelMeta panel={panel} />
        <div className="blood-tests__scroll">
          <table className="blood-tests__table">
            <thead>
              <tr>
                <th>Marker</th>
                <th>Value</th>
                <th>Unit</th>
                <th>Reference range</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {panel.results.map((r) => (
                <ResultRow key={r.id} result={r} />
              ))}
            </tbody>
          </table>
        </div>
        <button
          type="button"
          className="button"
          disabled={deletePanel.isPending}
          onClick={() => deletePanel.mutate(panel.localDate)}
        >
          {deletePanel.isPending ? "Deleting…" : "Delete this test"}
        </button>
      </div>
    </details>
  );
}

interface MarkerDraft {
  marker: string;
  value: string;
  unit: string;
  refLow: string;
  refHigh: string;
}

function emptyMarker(): MarkerDraft {
  return { marker: "", value: "", unit: "", refLow: "", refHigh: "" };
}

function AddForm({ onCancel, onDone }: { onCancel: () => void; onDone: () => void }) {
  const createBatch = useCreateBloodTestBatch();
  const [localDate, setLocalDate] = useState(TODAY);
  const [labName, setLabName] = useState("");
  const [notes, setNotes] = useState("");
  const [markers, setMarkers] = useState<MarkerDraft[]>([emptyMarker()]);
  const [error, setError] = useState<string | null>(null);

  function updateMarker(index: number, patch: Partial<MarkerDraft>) {
    setMarkers((prev) => prev.map((m, i) => (i === index ? { ...m, ...patch } : m)));
  }

  function removeMarker(index: number) {
    setMarkers((prev) => prev.filter((_, i) => i !== index));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const results: BloodTestMarkerIn[] = [];
    for (const m of markers) {
      if (m.marker.trim() === "" && m.value.trim() === "") continue; // silently skip blank rows
      const value = Number(m.value);
      if (m.marker.trim() === "" || !Number.isFinite(value)) {
        setError("Every marker needs a name and a numeric value.");
        return;
      }
      results.push({
        marker: m.marker.trim(),
        value_num: value,
        unit: m.unit.trim() === "" ? null : m.unit.trim(),
        reference_low: m.refLow.trim() === "" ? null : Number(m.refLow),
        reference_high: m.refHigh.trim() === "" ? null : Number(m.refHigh),
      });
    }
    if (results.length === 0) {
      setError("Add at least one marker.");
      return;
    }
    createBatch.mutate(
      {
        local_date: localDate,
        lab_name: labName.trim() === "" ? null : labName.trim(),
        notes: notes.trim() === "" ? null : notes.trim(),
        results,
      },
      { onSuccess: () => onDone() },
    );
  }

  return (
    <form className="blood-tests__form" onSubmit={handleSubmit}>
      <div className="blood-tests__form-header">
        <label>
          Date
          <input
            className="input"
            type="date"
            value={localDate}
            max={TODAY}
            onChange={(e) => setLocalDate(e.target.value)}
            required
          />
        </label>
        <label>
          Lab (optional)
          <input
            className="input"
            value={labName}
            onChange={(e) => setLabName(e.target.value)}
            placeholder="e.g. Quest Diagnostics"
          />
        </label>
      </div>
      <label>
        Notes (optional)
        <textarea
          className="input"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="e.g. Fasting draw"
        />
      </label>

      <div className="blood-tests__scroll">
        <table className="blood-tests__table">
          <thead>
            <tr>
              <th>Marker</th>
              <th>Value</th>
              <th>Unit</th>
              <th>Reference low</th>
              <th>Reference high</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {markers.map((m, i) => (
              <tr key={i}>
                <td>
                  <input
                    className="input blood-tests__cell-input"
                    value={m.marker}
                    onChange={(e) => updateMarker(i, { marker: e.target.value })}
                    placeholder="e.g. LDL Cholesterol"
                  />
                </td>
                <td>
                  <input
                    className="input blood-tests__cell-input"
                    type="number"
                    step="any"
                    value={m.value}
                    onChange={(e) => updateMarker(i, { value: e.target.value })}
                  />
                </td>
                <td>
                  <input
                    className="input blood-tests__cell-input"
                    value={m.unit}
                    onChange={(e) => updateMarker(i, { unit: e.target.value })}
                    placeholder="mg/dL"
                  />
                </td>
                <td>
                  <input
                    className="input blood-tests__cell-input"
                    type="number"
                    step="any"
                    value={m.refLow}
                    onChange={(e) => updateMarker(i, { refLow: e.target.value })}
                  />
                </td>
                <td>
                  <input
                    className="input blood-tests__cell-input"
                    type="number"
                    step="any"
                    value={m.refHigh}
                    onChange={(e) => updateMarker(i, { refHigh: e.target.value })}
                  />
                </td>
                <td>
                  {markers.length > 1 && (
                    <button type="button" className="button" onClick={() => removeMarker(i)}>
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" className="button" onClick={() => setMarkers((prev) => [...prev, emptyMarker()])}>
        + Add another marker
      </button>

      {error && (
        <span role="alert" className="blood-tests__error">
          {error}
        </span>
      )}
      {createBatch.isError && !error && (
        <span role="alert" className="blood-tests__error">
          Could not save.
        </span>
      )}

      <div className="blood-tests__form-actions">
        <button type="submit" className="button button--primary" disabled={createBatch.isPending}>
          {createBatch.isPending ? "Saving…" : "Save blood test"}
        </button>
        <button type="button" className="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export function BloodTestsPanel() {
  const [isAdding, setIsAdding] = useState(false);
  const results = useBloodTests(EARLIEST_PLAUSIBLE_DATE, TODAY);

  const panels = useMemo(() => groupIntoPanels(results.data ?? []), [results.data]);

  return (
    <section className="card">
      <h2>Blood tests</h2>
      <p className="chart-note">
        Your own lab results, entered by hand. Reference ranges (when given) come from your own
        lab report -- shown here only to flag values outside the range you provided, not as
        medical advice.
      </p>

      {results.isLoading && <LoadingSpinner />}
      {results.isError && <p role="alert">Could not load blood test results.</p>}

      {!results.isLoading && !results.isError && panels.length === 0 && !isAdding && (
        <p className="chart-note">No blood tests recorded yet.</p>
      )}

      {panels.length > 0 && (
        <div className="blood-tests__panels">
          {panels.map((panel, i) => (
            <PanelCard key={panel.localDate} panel={panel} defaultOpen={i === 0} />
          ))}
        </div>
      )}

      {isAdding ? (
        <AddForm onCancel={() => setIsAdding(false)} onDone={() => setIsAdding(false)} />
      ) : (
        <button type="button" className="button" onClick={() => setIsAdding(true)}>
          + Add blood test
        </button>
      )}
    </section>
  );
}
