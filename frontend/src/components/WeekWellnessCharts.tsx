// The week view's Wellness section: resting heart rate, stress, sleep duration, and sleep vs
// waking respiration, each a small daily chart across the week's 7 days. Resting HR/stress/
// respiration reuse HealthTrendChart (the same component MonthView's own Health card already
// uses) rather than a second implementation; sleep duration comes from GET /sleep, a different
// endpoint HealthTrendChart doesn't read, so it gets its own small bar chart here.
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { HealthDashboardMetricOut, SleepSessionOut } from "../api/types";
import { eachDate } from "../dateUtils";
import { HealthTrendChart } from "./HealthTrendChart";

function formatDayTick(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
}

function hasDailyData(metric: HealthDashboardMetricOut | undefined): boolean {
  return (metric?.daily ?? []).some((d) => d.value_avg != null || d.value_last != null);
}

export function WeekWellnessCharts({
  metrics,
  sleepSessions,
  weekStart,
  weekEnd,
}: {
  metrics: HealthDashboardMetricOut[];
  sleepSessions: SleepSessionOut[];
  weekStart: string;
  weekEnd: string;
}) {
  const restingHr = metrics.find((m) => m.logical_metric === "resting_heart_rate");
  const stress = metrics.find((m) => m.logical_metric === "stress_average");
  const wakingResp = metrics.find((m) => m.logical_metric === "waking_respiration_rate");
  const sleepResp = metrics.find((m) => m.logical_metric === "sleep_respiration_rate");
  // Body composition (Eufy) is read irregularly, not daily like the rest of this section --
  // still worth a chart when a weigh-in landed this week.
  const weight = metrics.find((m) => m.logical_metric === "weight_kg");

  const sleepData = eachDate(weekStart, weekEnd).map((d) => {
    const session = sleepSessions.find((s) => s.local_date === d);
    return {
      local_date: d,
      hours: session?.total_sleep_s != null ? Math.round((session.total_sleep_s / 3600) * 10) / 10 : null,
    };
  });

  const hasRestingHr = hasDailyData(restingHr);
  const hasStress = hasDailyData(stress);
  const hasSleepData = sleepData.some((p) => p.hours != null);
  const hasRespiration = hasDailyData(wakingResp) || hasDailyData(sleepResp);
  const hasWeight = hasDailyData(weight);

  if (!hasRestingHr && !hasStress && !hasSleepData && !hasRespiration && !hasWeight) return null;

  return (
    <section className="card">
      <h2>Wellness</h2>

      {hasRestingHr && (
        <>
          <h3>Resting heart rate</h3>
          <HealthTrendChart metrics={metrics} keys={["resting_heart_rate"]} />
        </>
      )}

      {hasStress && (
        <>
          <h3>Stress</h3>
          <HealthTrendChart metrics={metrics} keys={["stress_average"]} />
        </>
      )}

      {hasSleepData && (
        <>
          <h3>Sleep duration</h3>
          <ResponsiveContainer width="100%" height={140}>
            <BarChart data={sleepData}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="local_date"
                tickFormatter={formatDayTick}
                stroke="var(--color-text-muted)"
                fontSize={11}
              />
              <YAxis stroke="var(--color-text-muted)" fontSize={11} width={32} unit="h" />
              <Tooltip
                formatter={(value) => [`${value} h`, "Sleep"]}
                contentStyle={{
                  background: "var(--color-surface-raised)",
                  border: "1px solid var(--color-border)",
                }}
              />
              <Bar dataKey="hours" fill="var(--color-cadence)" isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </>
      )}

      {hasRespiration && (
        <>
          <h3>Respiration (sleep vs waking)</h3>
          <HealthTrendChart metrics={metrics} keys={["sleep_respiration_rate", "waking_respiration_rate"]} />
        </>
      )}

      {hasWeight && (
        <>
          <h3>Weight</h3>
          <HealthTrendChart metrics={metrics} keys={["weight_kg"]} />
        </>
      )}
    </section>
  );
}
