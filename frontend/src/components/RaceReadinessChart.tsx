// Insights' "Race Readiness" tab: has the athlete actually run enough *volume* for their next
// scheduled race, not just "are they fit" -- see race_readiness.py's own module docstring for
// the full model (recency-weighted weekly-distance/long-run compliance, combined into one
// readiness percentage). predicted_duration_s reuses the existing VDOT-based race-time
// prediction (the same value planned_race.predicted_duration_s already shows), displayed
// alongside readiness -- a fitness-derived time and a volume-adequacy fraction have different
// physiological bases, so this app never blends them into one fabricated number.
import { useRaceReadiness } from "../api/queries";
import type { RaceReadinessPointOut } from "../api/types";
import { toneColor } from "../metricStyle";
import { formatClockDuration } from "../runningStats";
import type { TrendPoint } from "../trendWindow";
import { ChartFullscreen } from "./ChartFullscreen";
import { LoadingSpinner } from "./LoadingSpinner";
import { RaceVolumeBarChart } from "./RaceVolumeBarChart";
import { StatTile } from "./StatTile";
import { TrendChart, type TrendSeries } from "./TrendChart";

function formatKm(m: number): string {
  return `${(m / 1000).toFixed(1)} km`;
}

// The backend already returns one point per week (race_readiness.py's own weekly cadence) --
// this just labels/orders them for TrendChart, unlike Vo2maxChart.tsx's own points, which still
// need bucketSeriesToWindow to aggregate a raw daily series client-side.
function toTrendPoint(p: RaceReadinessPointOut): TrendPoint {
  const d = new Date(`${p.as_of}T00:00:00Z`);
  return {
    x: d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }),
    ts: d.getTime(),
    readiness: p.readiness_pct,
    weekly: p.weekly_distance_compliance_pct,
    longRun: p.long_run_compliance_pct,
  };
}

const READINESS_SERIES: TrendSeries[] = [
  {
    key: "readiness",
    label: "Readiness",
    color: toneColor("pace"),
    formatValue: (v) => `${v.toFixed(0)}%`,
  },
  {
    key: "weekly",
    label: "Weekly distance",
    color: toneColor("elevation"),
    formatValue: (v) => `${v.toFixed(0)}%`,
  },
  {
    key: "longRun",
    label: "Long run",
    color: toneColor("cadence"),
    formatValue: (v) => `${v.toFixed(0)}%`,
  },
];

export function RaceReadinessChart() {
  const readiness = useRaceReadiness();

  if (readiness.isLoading) return <LoadingSpinner />;
  if (readiness.isError) return <p role="alert">Could not load race readiness.</p>;
  if (!readiness.data) return null;

  if (!readiness.data.available || !readiness.data.current) {
    return (
      <section className="card">
        <h2>Race Readiness</h2>
        <p className="chart-note">
          Add an upcoming race under a day or week's "Race" card on the calendar to see how your
          training volume compares to what it typically takes to run it.
        </p>
      </section>
    );
  }

  const { race_name, race_local_date, weekly_distance_target_m, long_run_target_m, current } =
    readiness.data;

  const points: TrendPoint[] = readiness.data.history.map(toTrendPoint);

  return (
    <section className="card">
      <h2>Race Readiness</h2>
      <p className="chart-note">
        For {race_name} on {race_local_date}: how your recent weekly running distance and long
        runs compare to what this app targets for that distance (
        {weekly_distance_target_m != null && `${formatKm(weekly_distance_target_m)}/week`}
        {weekly_distance_target_m != null && long_run_target_m != null && ", "}
        {long_run_target_m != null && `${formatKm(long_run_target_m)} long run`}) -- recent weeks
        count for more than older ones. This is a volume-adequacy check, not a fitness estimate;
        see the Race Predictions tab for your VDOT-based finish-time prediction, shown below
        alongside it.
      </p>
      <div className="stat-grid">
        <StatTile
          label="Readiness"
          value={current.readiness_pct.toFixed(0)}
          unit="%"
          icon="trophy"
          tone="pace"
          hero
        />
        <StatTile
          label="Weekly distance"
          value={current.weekly_distance_compliance_pct.toFixed(0)}
          unit="%"
          meta={weekly_distance_target_m != null ? `Target: ${formatKm(weekly_distance_target_m)}/week` : null}
          icon="route"
          tone="elevation"
        />
        <StatTile
          label="Long run"
          value={current.long_run_compliance_pct.toFixed(0)}
          unit="%"
          meta={long_run_target_m != null ? `Target: ${formatKm(long_run_target_m)}` : null}
          icon="mountain"
          tone="cadence"
        />
        {readiness.data.predicted_duration_s != null && (
          <StatTile
            label="Prognosis"
            value={formatClockDuration(readiness.data.predicted_duration_s)}
            icon="clock"
            tone="load"
          />
        )}
      </div>
      <ChartFullscreen as="h3" title="Readiness over time">
        <TrendChart points={points} series={READINESS_SERIES} />
      </ChartFullscreen>
      <ChartFullscreen as="h3" title="Weekly distance (last 182 days)">
        <RaceVolumeBarChart
          weeks={readiness.data.weekly_distance_series}
          targetM={weekly_distance_target_m}
          color={toneColor("elevation")}
        />
      </ChartFullscreen>
      <ChartFullscreen as="h3" title="Long run (last 70 days)">
        <RaceVolumeBarChart
          weeks={readiness.data.long_run_series}
          targetM={long_run_target_m}
          color={toneColor("cadence")}
        />
      </ChartFullscreen>
    </section>
  );
}
