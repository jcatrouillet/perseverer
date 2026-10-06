import { Link } from "wouter";

import {
  useActivities,
  useAllActivities,
  useCalendarMonths,
  useClimbingSummary,
  useFitness,
  useHealthDashboard,
  useSleep,
} from "../../api/queries";
import { ChartFullscreen } from "../../components/ChartFullscreen";
import { DateNavigator } from "../../components/DateNavigator";
import { FitnessChart } from "../../components/FitnessChart";
import { GoalButton } from "../../components/GoalButton";
import { PeriodShareButton } from "../../components/ShareButton";
import { HealthTrendChart } from "../../components/HealthTrendChart";
import { HealthMetricTiles } from "../../components/HealthMetricTiles";
import { ClimbingStatsCard } from "../../components/ClimbingStatsCard";
import { HikeStatsCard } from "../../components/HikeStatsCard";
import { LoadingSpinner } from "../../components/LoadingSpinner";
import { PeriodStatsCard } from "../../components/PeriodStatsCard";
import { RunningStats } from "../../components/RunningStats";
import { SleepDurationChart } from "../../components/SleepDurationChart";
import { monthName, yearRange } from "../../dateUtils";
import { useDistanceFormat } from "../../formatDistance";
import { anyMetricHasData, weeklyAverageSleepHours } from "../../healthStats";
import { CORE_METRICS, HRV_METRIC, WEIGHT_METRIC } from "../../healthMetricGroups";
import { personalRecords } from "../../runningStats";
import { busiestMonth } from "../../yearStats";
import "../../styles/calendar.css";

function formatWeekTick(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function YearView({ year }: { year: number }) {
  const { metersToDisplay, unitLabel } = useDistanceFormat();
  const { start, end } = yearRange(year);
  const priorYear = yearRange(year - 1);
  const months = useCalendarMonths(start, end);
  // Rollup-backed (not a raw activity fetch) -- reuses the same /calendar/months endpoint the
  // month-tile grid below already calls, summed across all 12 months, for the year-over-year
  // delta tile (ADR 0011 decision 3).
  const priorYearMonths = useCalendarMonths(priorYear.start, priorYear.end);
  const fitness = useFitness(start, end);
  const health = useHealthDashboard(start, end);
  const sleep = useSleep(start, end);
  const climbing = useClimbingSummary(start, end);
  const allActivities = useActivities({ startDate: start, endDate: end, limit: 500 });
  const runs = useActivities({ sport: "running", startDate: start, endDate: end, limit: 500 });
  const hikes = useActivities({ sport: "hiking", startDate: start, endDate: end, limit: 500 });
  // Unbounded, all-history fetch (distinct from `runs`' period-scoped one) so RunningStats can
  // tell a genuine all-time PR apart from merely "fastest within this year" -- see ADR 0011
  // decision 3 and RunningStats.tsx's allTimeRecords prop docstring.
  const allTimeRunning = useAllActivities({ sport: "running" });
  const byMonth = new Map(months.data?.periods.map((p) => [p.period_start.slice(5, 7), p]));
  const weeklySleep = weeklyAverageSleepHours(sleep.data ?? [], start, end);

  const all = allActivities.data?.items ?? [];
  const busiest = busiestMonth(all);
  const priorYearDistances = priorYearMonths.data?.periods
    .map((p) => p.activity_distance_m)
    .filter((v): v is number => v != null);
  const priorYearDistanceM =
    priorYearDistances != null && priorYearDistances.length > 0
      ? priorYearDistances.reduce((a, b) => a + b, 0)
      : null;
  // Running-only prior-year distance for RunningStats' own comparison (distinct from
  // priorYearDistanceM above, which is all-sport) -- filtered client-side from the unbounded
  // `allTimeRunning` fetch already made for PR detection, rather than a third network call.
  // Null (not 0) while that fetch is still loading, so the tile doesn't briefly show a false zero.
  const priorYearRunningDistanceM = allTimeRunning.data
    ? allTimeRunning.data
        .filter(
          (a) =>
            a.local_date != null &&
            a.local_date >= priorYear.start &&
            a.local_date <= priorYear.end,
        )
        .reduce((sum, a) => sum + (a.distance_m ?? 0), 0)
    : null;

  return (
    <main>
      <DateNavigator year={year} />
      <div className="calendar-header">
        <h1>{year}</h1>
        <GoalButton periodType="year" periodStart={String(year)} periodLabel={String(year)} />
        <PeriodShareButton periodType="year" periodStart={String(year)} />
      </div>
      {months.isLoading && <LoadingSpinner />}
      {months.isError && <p role="alert">Could not load the year.</p>}

      <PeriodStatsCard
        title={`${year} stats`}
        activities={all}
        busiestLabel="Busiest month"
        busiestValue={busiest != null ? monthName(busiest) : null}
        compareLabel={String(year - 1)}
        compareDistanceM={priorYearDistanceM}
      />

      {runs.data && (
        <RunningStats
          activities={runs.data.items}
          startDate={start}
          endDate={end}
          periodLabel={String(year)}
          trailingWindowDays={90}
          allTimeRecords={personalRecords(allTimeRunning.data ?? [])}
          compareLabel={String(year - 1)}
          compareDistanceM={priorYearRunningDistanceM}
        />
      )}

      {hikes.data && <HikeStatsCard activities={hikes.data.items} />}
      <ClimbingStatsCard summary={climbing.data} />

      <section className="card">
        {fitness.data ? (
          <ChartFullscreen as="h2" title="Fitness & Form">
            <FitnessChart series={fitness.data} />
          </ChartFullscreen>
        ) : (
          <h2>Fitness &amp; Form</h2>
        )}
        {fitness.isLoading && <LoadingSpinner />}
        {fitness.isError && <p role="alert">Could not load Fitness &amp; Form.</p>}
      </section>

      <section className="card">
        <h2>Health</h2>
        {health.isLoading && <LoadingSpinner />}
        {health.isError && <p role="alert">Could not load health data.</p>}
        {health.data && (
          <>
            {anyMetricHasData(health.data.metrics, CORE_METRICS) && (
              <>
                <h3>Core daily summary — average over {year}</h3>
                <HealthMetricTiles metrics={health.data.metrics} keys={CORE_METRICS} />
              </>
            )}

            {weeklySleep.some((p) => p.avgHours != null) && (
              <ChartFullscreen as="h3" title={`Average weekly sleep — over ${year}`}>
                <SleepDurationChart
                  data={weeklySleep.map((p) => ({ x: p.weekStart, hours: p.avgHours }))}
                  tickFormatter={formatWeekTick}
                  tooltipLabelFormatter={(x) => `Week of ${formatWeekTick(x)}`}
                  interval={Math.max(0, Math.ceil(weeklySleep.length / 8) - 1)}
                />
              </ChartFullscreen>
            )}

            {anyMetricHasData(health.data.metrics, HRV_METRIC) && (
              <ChartFullscreen as="h3" title={`HRV — over ${year}`}>
                <HealthTrendChart metrics={health.data.metrics} keys={HRV_METRIC} />
              </ChartFullscreen>
            )}

            {anyMetricHasData(health.data.metrics, WEIGHT_METRIC) && (
              <ChartFullscreen as="h3" title={`Weight — over ${year}`}>
                <HealthTrendChart metrics={health.data.metrics} keys={WEIGHT_METRIC} />
              </ChartFullscreen>
            )}
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
                    <div>
                      {metersToDisplay(rollup.activity_distance_m).toFixed(1)} {unitLabel}
                    </div>
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
