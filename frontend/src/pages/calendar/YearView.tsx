import { Link } from "wouter";

import { useActivities, useCalendarMonths, useFitness, useHealthDashboard } from "../../api/queries";
import { DateNavigator } from "../../components/DateNavigator";
import { FitnessChart } from "../../components/FitnessChart";
import { HealthTrendChart } from "../../components/HealthTrendChart";
import { HealthMetricTiles } from "../../components/HealthMetricTiles";
import { PeriodStatsCard } from "../../components/PeriodStatsCard";
import { RunningStats } from "../../components/RunningStats";
import { monthName, yearRange } from "../../dateUtils";
import { CORE_METRICS, HRV_SPO2_STRESS_METRICS } from "../HealthPage";
import { busiestMonth } from "../../yearStats";
import "../../styles/calendar.css";

export function YearView({ year }: { year: number }) {
  const { start, end } = yearRange(year);
  const months = useCalendarMonths(start, end);
  const fitness = useFitness(start, end);
  const health = useHealthDashboard(start, end);
  const allActivities = useActivities({ startDate: start, endDate: end, limit: 500 });
  const runs = useActivities({ sport: "running", startDate: start, endDate: end, limit: 500 });
  const byMonth = new Map(months.data?.periods.map((p) => [p.period_start.slice(5, 7), p]));

  const all = allActivities.data?.items ?? [];
  const busiest = busiestMonth(all);

  return (
    <main>
      <DateNavigator year={year} />
      <h1>{year}</h1>
      {months.isLoading && <p>Loading…</p>}
      {months.isError && <p role="alert">Could not load the year.</p>}

      <PeriodStatsCard
        title={`${year} stats`}
        activities={all}
        busiestLabel="Busiest month"
        busiestValue={busiest != null ? monthName(busiest) : null}
      />

      {runs.data && (
        <RunningStats
          activities={runs.data.items}
          startDate={start}
          endDate={end}
          periodLabel={String(year)}
          trailingWindowDays={90}
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
            <h3>Core daily summary — average over {year}</h3>
            <HealthMetricTiles metrics={health.data.metrics} keys={CORE_METRICS} />

            <h3>HRV / SpO2 / Stress — over {year}</h3>
            <HealthTrendChart metrics={health.data.metrics} keys={HRV_SPO2_STRESS_METRICS} />
          </>
        )}
      </section>

      <div className="stat-grid month-tile-grid">
        {Array.from({ length: 12 }, (_, i) => i + 1).map((month) => {
          const rollup = byMonth.get(String(month).padStart(2, "0"));
          return (
            <Link key={month} href={`/calendar/${year}/${month}`} className="card month-tile">
              <div className="month-tile__name">{monthName(month)}</div>
              {rollup && rollup.activity_count > 0 ? (
                <div className="month-tile__stats">
                  <div>
                    {rollup.activity_count} activit{rollup.activity_count === 1 ? "y" : "ies"}
                  </div>
                  {rollup.activity_distance_m != null && (
                    <div>{(rollup.activity_distance_m / 1000).toFixed(1)} km</div>
                  )}
                  {rollup.activity_moving_duration_s != null && (
                    <div>{(rollup.activity_moving_duration_s / 3600).toFixed(1)}h</div>
                  )}
                </div>
              ) : (
                <div className="month-tile__stats month-tile__stats--empty">No activity</div>
              )}
            </Link>
          );
        })}
      </div>
    </main>
  );
}
