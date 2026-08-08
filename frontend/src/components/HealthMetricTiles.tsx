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
  return (
    <div className="stat-grid">
      {keys.map((key) => {
        const metric = metrics.find((m) => m.logical_metric === key);
        const avg = weightedAverage(metric);
        const style = healthMetricStyle(key);
        return (
          <StatTile
            key={key}
            label={key.replace(/_/g, " ")}
            value={avg != null ? avg.toFixed(1) : "—"}
            icon={style.icon}
            tone={style.tone}
            hero={HERO_METRICS.has(key)}
          />
        );
      })}
    </div>
  );
}
