// Horizontal bar of time spent in each HR zone. A plain CSS bar list rather than
// a Recharts BarChart: each row needs a boundary-range label to one side and a duration to the
// other, which a bar chart's own axis labelling doesn't give a clean way to do at this small a
// size -- ChartLegend/StatTile already established that a hand-built HTML row is fine for this
// kind of small, structured readout (see the running heatmap's own legend for precedent).
import { computeHrZonesFromStream, extractHrZones, hrZoneRangeLabel } from "../activityMetrics";
import type { ActivityMetricOut } from "../api/types";
import { formatDurationHM } from "../runningStats";

// Cool-to-hot progression across the real tone palette, one per zone -- not a single flat
// colour, since the whole point of the chart is to distinguish how effort was spread across
// zones. A device with more or fewer zones just cycles/truncates this list. Each zone here is
// its own full-width, individually-labeled row, so two visually-similar neighbors are still
// legible -- unlike WorkoutLoadBar.tsx's own zone bar (many adjacent unlabeled segments, often
// packed narrow), which needs a higher-contrast dedicated gradient of its own
// (--color-zone-1..5, theme.css) rather than reusing this list.
const ZONE_TONES = ["cadence", "elevation", "pace", "load", "power", "hr", "hr"] as const;

export function TimeInZoneChart({
  metrics,
  heartRateStream,
  timestamps,
  configuredZoneBoundaries,
  speedMpsStream,
}: {
  metrics: ActivityMetricOut[];
  /** The activity's own per-second HR stream + timestamps, and the athlete's own configured
   * zone boundaries (Settings tab) -- when both are present, zones are computed from these
   * instead of the device-reported `metrics`, so the chart reflects the zones the athlete
   * actually asked for. Omitted (e.g. by existing callers/tests that don't pass them) falls
   * back to the device-reported zones unchanged. */
  heartRateStream?: (number | null)[] | null;
  timestamps?: string[] | null;
  configuredZoneBoundaries?: [number, number, number, number] | null;
  /** Same-length speed stream, index-aligned with heartRateStream/timestamps -- excludes a
   * stopped interval from the computed total so it matches this app's moving-time convention
   * elsewhere (and intervals.icu's own). Only used by the stream-computed path; the device-
   * reported `extractHrZones` fallback already reflects however the device itself defined it. */
  speedMpsStream?: (number | null)[] | null;
}) {
  const zones =
    configuredZoneBoundaries != null && heartRateStream != null && timestamps != null
      ? computeHrZonesFromStream(
          heartRateStream,
          timestamps,
          configuredZoneBoundaries,
          speedMpsStream,
        )
      : extractHrZones(metrics);
  if (!zones || zones.every((z) => z.seconds === 0)) return null;

  // Widths are relative to the activity's total time-in-zone, not the single largest zone --
  // scaling against the max zone was a real bug: one dominant zone (e.g. 90% of a run spent in
  // Z3) rendered at 100% width while every other real, nonzero zone shrank to a near-invisible
  // sliver (under 1% wide), reading as "broken" even though the underlying data was correct.
  const totalSeconds = zones.reduce((sum, z) => sum + z.seconds, 0) || 1;

  return (
    <div className="time-in-zone">
      {zones.map((zone) => (
        <div
          className={`time-in-zone__bar-row tone-${ZONE_TONES[zone.index % ZONE_TONES.length]}`}
          key={zone.index}
        >
          <span>
            Z{zone.index} <span className="faint">{hrZoneRangeLabel(zone)}</span>
          </span>
          <span className="time-in-zone__bar-track">
            <span
              className="time-in-zone__bar-fill"
              style={{ width: `${(zone.seconds / totalSeconds) * 100}%` }}
            />
          </span>
          <span className="time-in-zone__bar-value">
            {zone.seconds > 0
              ? `${formatDurationHM(zone.seconds)} · ${Math.round((zone.seconds / totalSeconds) * 100)}%`
              : "—"}
          </span>
        </div>
      ))}
      <p className="chart-note">
        Total {formatDurationHM(totalSeconds)} across {zones.length} zones.
      </p>
    </div>
  );
}
