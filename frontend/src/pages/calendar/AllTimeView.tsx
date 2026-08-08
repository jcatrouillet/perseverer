// The "All" link's destination -- same shape as YearView (period stats, Running, Fitness &
// Form, Health) but scoped to every activity ever recorded instead of one calendar year.
// Built entirely from useAllActivities (paginated GET /activities, see api/queries.ts) rather
// than a new backend endpoint, matching the client-aggregation pattern YearView/MonthView
// already use -- the one difference is pagination, since "all time" can exceed a single
// request's 500-row cap in a way a single year never does yet.
import { useAllActivities, useFitness, useHealthDashboard } from "../../api/queries";
import { DateNavigator } from "../../components/DateNavigator";
import { FitnessChart } from "../../components/FitnessChart";
import { HealthMetricTiles } from "../../components/HealthMetricTiles";
import { HealthTrendChart } from "../../components/HealthTrendChart";
import { PeriodStatsCard } from "../../components/PeriodStatsCard";
import { RunningStats } from "../../components/RunningStats";
import { EARLIEST_PLAUSIBLE_DATE, isoDate } from "../../dateUtils";
import { CORE_METRICS, HRV_SPO2_STRESS_METRICS } from "../HealthPage";
import { busiestYear } from "../../yearStats";
import "../../styles/calendar.css";

export function AllTimeView() {
  const allActivities = useAllActivities({});
  // Excludes the handful of GPS-clock-bug dates (see EARLIEST_PLAUSIBLE_DATE) -- otherwise
  // "all time" would nonsensically claim history back to 1989 and the heatmap below would try
  // to render a multi-decade grid over a few real years of data.
  const all = (allActivities.data ?? []).filter(
    (a) => a.local_date != null && a.local_date >= EARLIEST_PLAUSIBLE_DATE,
  );
  const dates = all.map((a) => a.local_date!).sort();
  const today = isoDate(new Date());
  const start = dates[0] ?? today;
  const end = dates[dates.length - 1] ?? today;

  const fitness = useFitness(start, end);
  const health = useHealthDashboard(start, end);
  const runs = all.filter((a) => a.sport === "running");
  const busiest = busiestYear(all);

  return (
    <main>
      <DateNavigator year={Number(end.slice(0, 4))} />
      <h1>All time</h1>
      {allActivities.isLoading && <p>Loading…</p>}
      {allActivities.isError && <p role="alert">Could not load all-time data.</p>}

      <PeriodStatsCard
        title="All time stats"
        activities={all}
        busiestLabel="Busiest year"
        busiestValue={busiest != null ? String(busiest) : null}
      />

      {runs.length > 0 && (
        <RunningStats
          activities={runs}
          startDate={start}
          endDate={end}
          periodLabel="all time"
          trailingWindowDays={365}
        />
      )}

      <section className="card">
        <h2>Fitness &amp; Form</h2>
        {fitness.isLoading && <p>Loading…</p>}
        {fitness.isError && <p role="alert">Could not load Fitness &amp; Form.</p>}
        {fitness.data && <FitnessChart series={fitness.data} />}
      </section>

      <section className="card">
        <h2>Health</h2>
        {health.isLoading && <p>Loading…</p>}
        {health.isError && <p role="alert">Could not load health data.</p>}
        {health.data && (
          <>
            <h3>Core daily summary — average over all time</h3>
            <HealthMetricTiles metrics={health.data.metrics} keys={CORE_METRICS} />

            <h3>HRV / SpO2 / Stress — over all time</h3>
            <HealthTrendChart metrics={health.data.metrics} keys={HRV_SPO2_STRESS_METRICS} />
          </>
        )}
      </section>
    </main>
  );
}
