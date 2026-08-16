// An athlete's own configured HR training zones -- see hr_zones.py's own docstring for the
// blended-formula rationale (heart rate reserve for zones 1-2, %threshold for zones 3-4).
// Setting these here is what switches TimeInZoneChart (activity detail) over from the device's
// own per-activity zone breakdown to zones computed from the raw HR stream against these three
// reference values -- see ActivityDetailPage.tsx's own wiring.
import { useEffect, useState } from "react";

import { useHrZoneConfig, useSetHrZoneConfig } from "../api/queries";
import { hrZoneRangeLabel } from "../activityMetrics";
import "../styles/settings.css";

function toInputValue(bpm: number | null): string {
  return bpm == null ? "" : String(bpm);
}

function parseField(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

export function SettingsPage() {
  const config = useHrZoneConfig();
  const mutation = useSetHrZoneConfig();

  const [maxHr, setMaxHr] = useState("");
  const [thresholdHr, setThresholdHr] = useState("");
  const [restingHr, setRestingHr] = useState("");

  // Pre-fill from the loaded config -- once, when it first arrives, not on every refetch (an
  // in-progress edit shouldn't be clobbered by a background refetch of the same query).
  useEffect(() => {
    if (!config.data) return;
    setMaxHr(toInputValue(config.data.max_hr_bpm));
    setThresholdHr(toInputValue(config.data.threshold_hr_bpm));
    setRestingHr(toInputValue(config.data.resting_hr_bpm));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.isSuccess]);

  const maxHrNum = parseField(maxHr);
  const thresholdHrNum = parseField(thresholdHr);
  const restingHrNum = parseField(restingHr);

  const clientError =
    maxHrNum != null && restingHrNum != null && maxHrNum <= restingHrNum
      ? "Max HR must be greater than resting HR."
      : maxHrNum != null && thresholdHrNum != null && thresholdHrNum > maxHrNum
        ? "Threshold HR can't exceed max HR."
        : null;

  const preview =
    mutation.data ?? (config.data?.max_hr_bpm != null ? config.data : null);
  const previewZones =
    preview?.zone1_high_bpm != null &&
    preview.zone2_high_bpm != null &&
    preview.zone3_high_bpm != null &&
    preview.zone4_high_bpm != null
      ? [
          { index: 1, seconds: 0, lowBoundary: null, highBoundary: preview.zone1_high_bpm },
          {
            index: 2,
            seconds: 0,
            lowBoundary: preview.zone1_high_bpm,
            highBoundary: preview.zone2_high_bpm,
          },
          {
            index: 3,
            seconds: 0,
            lowBoundary: preview.zone2_high_bpm,
            highBoundary: preview.zone3_high_bpm,
          },
          {
            index: 4,
            seconds: 0,
            lowBoundary: preview.zone3_high_bpm,
            highBoundary: preview.zone4_high_bpm,
          },
          { index: 5, seconds: 0, lowBoundary: preview.zone4_high_bpm, highBoundary: null },
        ]
      : [];

  return (
    <main>
      <h1>Settings</h1>

      <section className="card">
        <h2>HR training zones</h2>
        <p className="chart-note">
          Five zones, derived from three reference values: zones 1-2 from heart rate reserve
          (Karvonen), zones 3-4 from lactate threshold HR. Leave blank to fall back to each
          activity's own device-reported zones.
        </p>
        <form
          className="settings-hr-zones__form"
          onSubmit={(e) => {
            e.preventDefault();
            if (clientError) return;
            mutation.mutate({
              max_hr_bpm: maxHrNum,
              threshold_hr_bpm: thresholdHrNum,
              resting_hr_bpm: restingHrNum,
            });
          }}
        >
          <label>
            Max HR (bpm)
            <input
              type="number"
              inputMode="numeric"
              value={maxHr}
              onChange={(e) => setMaxHr(e.target.value)}
              placeholder="e.g. 190"
            />
          </label>
          <label>
            Threshold HR (bpm)
            <input
              type="number"
              inputMode="numeric"
              value={thresholdHr}
              onChange={(e) => setThresholdHr(e.target.value)}
              placeholder="e.g. 168"
            />
          </label>
          <label>
            Resting HR (bpm)
            <input
              type="number"
              inputMode="numeric"
              value={restingHr}
              onChange={(e) => setRestingHr(e.target.value)}
              placeholder="e.g. 48"
            />
          </label>
          <button type="submit" disabled={mutation.isPending || clientError != null}>
            {mutation.isPending ? "Saving…" : "Save"}
          </button>
          {clientError && (
            <span role="alert" className="settings-hr-zones__error">
              {clientError}
            </span>
          )}
          {mutation.isError && !clientError && (
            <span role="alert" className="settings-hr-zones__error">
              Could not save -- check the values are sane (max &gt; resting, threshold ≤ max).
            </span>
          )}
          {mutation.isSuccess && (
            <span className="settings-hr-zones__saved">Saved.</span>
          )}
        </form>

        {previewZones.length > 0 && (
          <table className="settings-hr-zones__preview">
            <thead>
              <tr>
                <th>Zone</th>
                <th>Range (bpm)</th>
              </tr>
            </thead>
            <tbody>
              {previewZones.map((zone) => (
                <tr key={zone.index}>
                  <td>Z{zone.index}</td>
                  <td>{hrZoneRangeLabel(zone)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
