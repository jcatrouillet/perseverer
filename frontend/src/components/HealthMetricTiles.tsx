// The "core daily summary" tile grid, shared by YearView, MonthView and AllTimeView -- all
// three previously carried their own byte-identical copy of this loop. Each value is the
// weighted average over whatever range the caller asked for.
import type { HealthDashboardMetricOut } from "../api/types";
import { weightedAverage } from "../healthStats";
import { healthMetricStyle } from "../metricStyle";
import { StatTile } from "./StatTile";

/** At most three promoted tiles per card -- see StatTile. These are the daily-life figures, as
 * opposed to the training ones the activity cards above already lead with. */
const HERO_METRICS = new Set(["steps", "resting_heart_rate", "vo2max"]);

export function HealthMetricTiles({
  metrics,
  keys,
}: {
  metrics: HealthDashboardMetricOut[];
  keys: string[];
}) {
  // A summary view should only show what it actually has -- a tile with nothing to report
  // (never "—") would just be visual noise next to tiles that do have a real value.
  const present = keys.filter((key) => {
    const metric = metrics.find((m) => m.logical_metric === key);
    return weightedAverage(metric) != null;
  });
  if (present.length === 0) {
    return null;
  }

  return (
    <div className="stat-grid">
      {present.map((key) => {
        const metric = metrics.find((m) => m.logical_metric === key);
        const avg = weightedAverage(metric);
        const style = healthMetricStyle(key);
        return (
          <StatTile
            key={key}
            label={key.replace(/_/g, " ")}
            value={avg!.toFixed(1)}
            icon={style.icon}
            tone={style.tone}
            hero={HERO_METRICS.has(key)}
          />
        );
      })}
    </div>
  );
}
