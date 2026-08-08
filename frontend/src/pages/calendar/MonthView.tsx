import { useState } from "react";
import { Link } from "wouter";

import {
  useActivities,
  useCalendar,
  useCalendarWeeks,
  useFitness,
  useHealthDashboard,
} from "../../api/queries";
import { DateNavigator } from "../../components/DateNavigator";
import { FitnessChart } from "../../components/FitnessChart";
import { HealthMetricTiles } from "../../components/HealthMetricTiles";
import { HealthTrendChart } from "../../components/HealthTrendChart";
import { NotesPanel } from "../../components/NotesPanel";
import { PeriodStatsCard } from "../../components/PeriodStatsCard";
import { RunningStats } from "../../components/RunningStats";
import { monthGridWeeks, monthName, monthRange, parseIsoDate } from "../../dateUtils";
import { CORE_METRICS, HRV_SPO2_STRESS_METRICS } from "../HealthPage";
import { busiestWeekStart } from "../../yearStats";
import "../../styles/calendar.css";

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function formatBusiestWeek(monday: string): string {
  const label = new Date(`${monday}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
  return `Week of ${label}`;
}

export function MonthView({ year, month }: { year: number; month: number }) {
  const { start, end } = monthRange(year, month);
  const calendar = useCalendar(start, end);
  const fitness = useFitness(start, end);
  const health = useHealthDashboard(start, end);
  const allActivities = useActivities({ startDate: start, endDate: end, limit: 500 });
  const runs = useActivities({ sport: "running", startDate: start, endDate: end, limit: 500 });
  const [expandedDate, setExpandedDate] = useState<string | null>(null);

  const weekRows = monthGridWeeks(year, month);
  // Query the padded grid range, not just the month's own start/end -- otherwise the first/last
  // row's week total is missing whenever that week's Monday falls in the adjacent month.
  const weeks = useCalendarWeeks(weekRows[0]![0]!, weekRows[weekRows.length - 1]![6]!);

  const dayByDate = new Map(calendar.data?.days.map((d) => [d.local_date, d]));
  const weekByStart = new Map(weeks.data?.periods.map((p) => [p.period_start, p]));

  const all = allActivities.data?.items ?? [];
  const busiestWeek = busiestWeekStart(all);
  const periodLabel = `${monthName(month)} ${year}`;

  return (
    <main>
      <DateNavigator year={year} month={month} />
      <h1>{periodLabel}</h1>
      {calendar.isLoading && <p>Loading…</p>}
      {calendar.isError && <p role="alert">Could not load the month.</p>}

      <PeriodStatsCard
        title={`${periodLabel} stats`}
        activities={all}
        busiestLabel="Busiest week"
        busiestValue={busiestWeek != null ? formatBusiestWeek(busiestWeek) : null}
      />

      {runs.data && (
        <RunningStats
          activities={runs.data.items}
          startDate={start}
          endDate={end}
          periodLabel={periodLabel}
          trailingWindowDays={7}
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
            <h3>Core daily summary — average over {periodLabel}</h3>
            <HealthMetricTiles metrics={health.data.metrics} keys={CORE_METRICS} />

            <h3>HRV / SpO2 / Stress — over {periodLabel}</h3>
            <HealthTrendChart metrics={health.data.metrics} keys={HRV_SPO2_STRESS_METRICS} />
          </>
        )}
      </section>

      <table className="month-grid">
        <thead>
          <tr>
            {WEEKDAY_LABELS.map((label) => (
              <th key={label}>{label}</th>
            ))}
            <th>Week</th>
          </tr>
        </thead>
        <tbody>
          {weekRows.map((week) => {
            const weekRollup = weekByStart.get(week[0]!);
            return (
              <tr key={week[0]}>
                {week.map((date) => {
                  const inMonth = date >= start && date <= end;
                  const day = dayByDate.get(date);
                  if (!inMonth) return <td key={date} />;
                  return (
                    <td key={date}>
                      <button
                        type="button"
                        className="month-grid__day-btn"
                        onClick={() => setExpandedDate(date)}
                      >
                        {parseIsoDate(date).getUTCDate()}
                      </button>
                      {day && day.activity_count > 0 && (
                        <div className="month-grid__summary">
                          {day.activity_count} act
                          {day.activity_distance_m != null &&
                            ` · ${(day.activity_distance_m / 1000).toFixed(1)}km`}
                          {day.activity_moving_duration_s != null &&
                            ` · ${(day.activity_moving_duration_s / 3600).toFixed(1)}h`}
                        </div>
                      )}
                    </td>
                  );
                })}
                <td>
                  {weekRollup && weekRollup.activity_count > 0 && (
                    <Link to={`/calendar/week/${week[0]}`}>
                      {weekRollup.activity_count} act
                      {weekRollup.activity_distance_m != null &&
                        ` · ${(weekRollup.activity_distance_m / 1000).toFixed(1)} km`}
                    </Link>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {expandedDate && (
        <section className="card">
          <h2>{expandedDate}</h2>
          <NotesPanel entityType="day" entityId={expandedDate} />
        </section>
      )}
    </main>
  );
}
