// Horizontal bar of time spent in each HR zone (Milestone C). A plain CSS bar list rather than
// a Recharts BarChart: each row needs a boundary-range label to one side and a duration to the
// other, which a bar chart's own axis labelling doesn't give a clean way to do at this small a
// size -- ChartLegend/StatTile already established that a hand-built HTML row is fine for this
// kind of small, structured readout (see the running heatmap's own legend for precedent).
import { extractHrZones, hrZoneRangeLabel } from "../activityMetrics";
import type { ActivityMetricOut } from "../api/types";
import { formatDurationHM } from "../runningStats";

// Cool-to-hot progression across the real tone palette, one per zone -- not a single flat
// colour, since the whole point of the chart is to distinguish how effort was spread across
// zones. A device with more or fewer zones just cycles/truncates this list.
const ZONE_TONES = ["cadence", "elevation", "pace", "load", "power", "hr", "hr"] as const;

export function TimeInZoneChart({ metrics }: { metrics: ActivityMetricOut[] }) {
  const zones = extractHrZones(metrics);
  if (!zones || zones.every((z) => z.seconds === 0)) return null;

  const totalSeconds = zones.reduce((sum, z) => sum + z.seconds, 0);
  const maxSeconds = Math.max(...zones.map((z) => z.seconds), 1);

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
              style={{ width: `${(zone.seconds / maxSeconds) * 100}%` }}
            />
          </span>
          <span className="time-in-zone__bar-value">
            {zone.seconds > 0 ? formatDurationHM(zone.seconds) : "—"}
          </span>
        </div>
      ))}
      <p className="chart-note">Total {formatDurationHM(totalSeconds)} across {zones.length} zones.</p>
    </div>
  );
}
