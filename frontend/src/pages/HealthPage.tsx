// Three sections per the confirmed Phase 6 scope: core daily summary, sleep, HRV/SpO2/stress.
// Core + HRV/SpO2/stress read GET /health/dashboard (the LOGICAL_METRICS alias-merge -- see
// api/routers/health.py); sleep reads the existing GET /sleep endpoint directly, since
// sleep_session is its own dedicated table, not part of the health_observation EAV metrics.
// See docs/adr/0009-phase-6-calendar-rollups-fitness-health.md.
import { useState } from "react";

import { useHealthDashboard, useSleep } from "../api/queries";
import type { HealthDashboardMetricOut } from "../api/types";
import { isoDate } from "../dateUtils";

// Exported so other pages (e.g. YearView's year-in-review stats) can reuse the exact same
// categorization rather than maintaining a second, driftable copy of this list.
export const CORE_METRICS = [
  "steps",
  "calories",
  "resting_heart_rate",
  "max_heart_rate",
  "floors_ascended",
  "vo2max",
];
export const HRV_SPO2_STRESS_METRICS = ["hrv_nightly_average", "spo2_average", "stress_average"];
// Eufy smart scale is the only source for these (see adapters/eufy.py) -- exported so the
// calendar rollup views (YearView/MonthView/AllTimeView) and the week/day views can reuse the
// exact same list rather than each maintaining their own copy.
export const BODY_COMPOSITION_METRICS = [
  "weight_kg",
  "bmi",
  "body_fat_pct",
  "muscle_mass_kg",
  "bone_mass_kg",
  "water_pct",
  "bmr_kcal",
  "visceral_fat",
  "metabolic_age",
  "protein_ratio_pct",
];
// The summary calendar views (YearView/MonthView/AllTimeView) trend only HRV and weight -- by
// request, SpO2, stress, and the remaining Eufy body-composition metrics (BMI, body fat %,
// muscle mass, bone mass, water %, BMR, visceral fat, metabolic age, protein ratio) are left out
// of those trend charts as low-value there. They're still visible in this page's own
// BODY_COMPOSITION_METRICS list section above.
export const HRV_METRIC = ["hrv_nightly_average"];
export const WEIGHT_METRIC = ["weight_kg"];

function defaultRange(): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 30);
  return { start: isoDate(start), end: isoDate(end) };
}

export function MetricSection({
  title,
  metrics,
  keys,
}: {
  title: string;
  metrics: HealthDashboardMetricOut[];
  keys: string[];
}) {
  const byKey = new Map(metrics.map((m) => [m.logical_metric, m]));
  const present = keys.map((k) => byKey.get(k)).filter((m): m is HealthDashboardMetricOut => !!m);

  return (
    <section>
      <h2>{title}</h2>
      {present.length === 0 && <p>No data for this section in the selected range.</p>}
      <ul>
        {present.map((m) => {
          const latest = m.daily[m.daily.length - 1];
          return (
            <li key={m.logical_metric}>
              <strong>{m.logical_metric.replace(/_/g, " ")}</strong>
              {latest ? (
                <>
                  {" "}
                  — latest {latest.value_last ?? latest.value_avg} on {latest.local_date}
                </>
              ) : (
                <> — no data in this range</>
              )}
              {m.last_observed && m.last_observed !== latest?.local_date && (
                <> (last observed: {m.last_observed})</>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function HealthPage() {
  const [range, setRange] = useState(defaultRange);
  const dashboard = useHealthDashboard(range.start, range.end);
  const sleep = useSleep(range.start, range.end);

  return (
    <main>
      <h1>Health</h1>
      <form>
        <label>
          From
          <input
            type="date"
            value={range.start}
            onChange={(e) => setRange((r) => ({ ...r, start: e.target.value }))}
          />
        </label>
        <label>
          To
          <input
            type="date"
            value={range.end}
            onChange={(e) => setRange((r) => ({ ...r, end: e.target.value }))}
          />
        </label>
      </form>

      {dashboard.isLoading && <p>Loading…</p>}
      {dashboard.isError && <p role="alert">Could not load the health dashboard.</p>}

      {dashboard.data && (
        <>
          <MetricSection
            title="Core daily summary"
            metrics={dashboard.data.metrics}
            keys={CORE_METRICS}
          />
          <MetricSection
            title="HRV / SpO2 / Stress"
            metrics={dashboard.data.metrics}
            keys={HRV_SPO2_STRESS_METRICS}
          />
          <MetricSection
            title="Body composition"
            metrics={dashboard.data.metrics}
            keys={BODY_COMPOSITION_METRICS}
          />
        </>
      )}

      <section>
        <h2>Sleep</h2>
        {sleep.isLoading && <p>Loading…</p>}
        {sleep.isError && <p role="alert">Could not load sleep data.</p>}
        {sleep.data && sleep.data.length === 0 && <p>No sleep sessions in this range.</p>}
        <ul>
          {sleep.data?.map((session) => (
            <li key={session.local_date}>
              {session.local_date}
              {session.total_sleep_s != null && ` — ${(session.total_sleep_s / 3600).toFixed(1)}h`}
              {session.sleep_score != null && ` · score ${session.sleep_score}`}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
