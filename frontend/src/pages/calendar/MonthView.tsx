import { lazy, Suspense, useEffect, useState } from "react";
import { Link } from "wouter";

import {
  useActivities,
  useAllActivities,
  useCalendar,
  useCalendarMonths,
  useClimbingSummary,
  useFitness,
  useHealthDashboard,
  usePlannedRacesForRange,
  usePlannedWorkoutsList,
  useSleep,
} from "../../api/queries";
import type { DayRollupOut, PlannedRaceOut, PlannedWorkoutListItemOut } from "../../api/types";
import { ChartFullscreen } from "../../components/ChartFullscreen";
import { DateNavigator } from "../../components/DateNavigator";
import { FitnessChart } from "../../components/FitnessChart";
import { GoalButton } from "../../components/GoalButton";
import { PeriodShareButton } from "../../components/ShareButton";
import { HealthMetricTiles } from "../../components/HealthMetricTiles";
import { HealthTrendChart } from "../../components/HealthTrendChart";
import { ClimbingStatsCard } from "../../components/ClimbingStatsCard";
import { HikeStatsCard } from "../../components/HikeStatsCard";
import { Icon } from "../../components/Icon";
import { LoadingSpinner } from "../../components/LoadingSpinner";
import { NotesPanel } from "../../components/NotesPanel";
import { PeriodStatsCard } from "../../components/PeriodStatsCard";
import { RunningStats } from "../../components/RunningStats";
import { ScheduleWorkoutForm } from "../../components/ScheduleWorkoutForm";
import { SleepDurationChart } from "../../components/SleepDurationChart";
import {
  eachDate,
  monthGridWeeks,
  monthName,
  monthRange,
  parseIsoDate,
  sumDayRollups,
  weekdayLabels,
} from "../../dateUtils";
import { useDistanceFormat } from "../../formatDistance";
import { useTimeFormat } from "../../formatTime";
import { anyMetricHasData } from "../../healthStats";
import { CORE_METRICS, HRV_METRIC, WEIGHT_METRIC } from "../../healthMetricGroups";
import { plannedWorkoutSportStyle } from "../../metricStyle";
import { usePersonalize } from "../../PersonalizeContext";
import { personalRecords } from "../../runningStats";
import { busiestWeekStart } from "../../yearStats";
import "../../styles/calendar.css";

// Code-split, not a static import -- see DayViewPage.tsx's own identical comment (the app-shell
// bundle is already right at vite-plugin-pwa's 2MB single-file precache limit, and this card
// only ever renders inside the expanded-day card below, not on first paint).
const PlannedRaceForm = lazy(() =>
  import("../../components/PlannedRaceForm").then((m) => ({ default: m.PlannedRaceForm })),
);

function formatMonthDayTick(iso: string): string {
  return String(parseIsoDate(iso).getUTCDate());
}

function formatBusiestWeek(monday: string): string {
  const label = new Date(`${monday}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
  return `Week of ${label}`;
}

export function MonthView({ year, month }: { year: number; month: number }) {
  const { week_start_day: weekStartDay } = usePersonalize();
  const { formatHHMM } = useTimeFormat();
  const { metersToDisplay, unitLabel } = useDistanceFormat();
  const { start, end } = monthRange(year, month);
  const priorYearRange = monthRange(year - 1, month);
  const weekRows = monthGridWeeks(year, month, weekStartDay);
  // The padded grid range, not just the month's own start/end -- otherwise the first/last row's
  // own week total (summed from these same rows below) would be missing whatever days of that
  // real week fall in the adjacent month.
  const gridStart = weekRows[0]![0]!;
  const gridEnd = weekRows[weekRows.length - 1]![6]!;
  const calendar = useCalendar(gridStart, gridEnd);
  // Rollup-backed single-month lookup for the year-over-year delta tile (ADR 0011 decision 3) --
  // reuses /calendar/months rather than fetching a whole prior-year month's raw activities.
  const priorYearMonth = useCalendarMonths(priorYearRange.start, priorYearRange.end);
  const fitness = useFitness(start, end);
  const health = useHealthDashboard(start, end);
  const sleep = useSleep(start, end);
  const climbing = useClimbingSummary(start, end);
  const allActivities = useActivities({ startDate: start, endDate: end, limit: 500 });
  const runs = useActivities({ sport: "running", startDate: start, endDate: end, limit: 500 });
  const hikes = useActivities({ sport: "hiking", startDate: start, endDate: end, limit: 500 });
  // Unbounded, all-history fetch (distinct from `runs`' period-scoped one) so RunningStats can
  // tell a genuine all-time PR apart from merely "fastest within this month" -- see ADR 0011
  // decision 3 and RunningStats.tsx's allTimeRecords prop docstring.
  const allTimeRunning = useAllActivities({ sport: "running" });
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  // Navigating month-to-month (DateNavigator, prev/next) re-renders this same MonthView
  // instance with new year/month props rather than mounting a fresh one, so expandedDate would
  // otherwise keep pointing at a day from whichever month was previously open -- confirmed real
  // (reported): the expanded card kept showing a date no longer even in the visible grid.
  useEffect(() => {
    setExpandedDate(null);
  }, [year, month]);

  const plannedWorkouts = usePlannedWorkoutsList(gridStart, gridEnd);
  const plannedRaces = usePlannedRacesForRange(gridStart, gridEnd);

  const dayByDate = new Map(calendar.data?.days.map((d) => [d.local_date, d]));
  // Summed from the same per-day rows above, not the Monday-keyed period_rollup
  // (useCalendarWeeks) -- keeps each row's own total correct for any weekStartDay, not just
  // Monday. See dateUtils.ts::sumDayRollups.
  const weekByStart = new Map(
    weekRows.map((row) => {
      const rowDays = row.map((d) => dayByDate.get(d)).filter((d): d is DayRollupOut => d != null);
      return [row[0]!, sumDayRollups(rowDays)];
    }),
  );
  // A day can hold more than one planned workout now -- group rather than collapse to the last
  // one, sorted the same way the day panel itself orders them (scheduled_time, nulls last, then
  // id/creation order).
  const plannedByDate = new Map<string, PlannedWorkoutListItemOut[]>();
  for (const w of plannedWorkouts.data ?? []) {
    const forDate = plannedByDate.get(w.local_date);
    if (forDate) {
      forDate.push(w);
    } else {
      plannedByDate.set(w.local_date, [w]);
    }
  }
  for (const workoutsForDate of plannedByDate.values()) {
    workoutsForDate.sort((a, b) => {
      if (a.scheduled_time !== b.scheduled_time) {
        if (a.scheduled_time == null) return 1;
        if (b.scheduled_time == null) return -1;
        return a.scheduled_time < b.scheduled_time ? -1 : 1;
      }
      return a.id - b.id;
    });
  }
  // Same "own row per date" grouping as plannedByDate above -- races are a separate small table
  // (planned_race), not another planned_workout sport tier, so this is its own map rather than
  // merged into plannedByDate.
  const racesByDate = new Map<string, PlannedRaceOut[]>();
  for (const r of plannedRaces.data ?? []) {
    const forDate = racesByDate.get(r.local_date);
    if (forDate) {
      forDate.push(r);
    } else {
      racesByDate.set(r.local_date, [r]);
    }
  }

  const all = allActivities.data?.items ?? [];
  const busiestWeek = busiestWeekStart(all, weekStartDay);
  const periodLabel = `${monthName(month)} ${year}`;
  const priorYearMonthDistanceM = priorYearMonth.data?.periods[0]?.activity_distance_m ?? null;
  const compareLabel = `${monthName(month).slice(0, 3)} ${year - 1}`;
  // Running-only prior-year-same-month distance for RunningStats' own comparison (distinct from
  // priorYearMonthDistanceM above, which is all-sport, rollup-backed) -- filtered client-side
  // from the unbounded `allTimeRunning` fetch already made for PR detection, rather than a
  // third network call. Null (not 0) while that fetch is still loading.
  const priorYearMonthRunningDistanceM = allTimeRunning.data
    ? allTimeRunning.data
        .filter(
          (a) =>
            a.local_date != null &&
            a.local_date >= priorYearRange.start &&
            a.local_date <= priorYearRange.end,
        )
        .reduce((sum, a) => sum + (a.distance_m ?? 0), 0)
    : null;

  return (
    <main>
      <DateNavigator year={year} month={month} />
      <div className="calendar-header">
        <h1>{periodLabel}</h1>
        <GoalButton
          periodType="month"
          periodStart={`${year}-${String(month).padStart(2, "0")}`}
          periodLabel={periodLabel}
        />
        <PeriodShareButton
          periodType="month"
          periodStart={`${year}-${String(month).padStart(2, "0")}`}
        />
      </div>
      {calendar.isLoading && <LoadingSpinner />}
      {calendar.isError && <p role="alert">Could not load the month.</p>}

      <PeriodStatsCard
        title={`${periodLabel} stats`}
        activities={all}
        busiestLabel="Busiest week"
        busiestValue={busiestWeek != null ? formatBusiestWeek(busiestWeek) : null}
        compareLabel={compareLabel}
        compareDistanceM={priorYearMonthDistanceM}
      />

      {runs.data && (
        <RunningStats
          activities={runs.data.items}
          startDate={start}
          endDate={end}
          periodLabel={periodLabel}
          trailingWindowDays={7}
          allTimeRecords={personalRecords(allTimeRunning.data ?? [])}
          compareLabel={compareLabel}
          compareDistanceM={priorYearMonthRunningDistanceM}
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
                <h3>Core daily summary — average over {periodLabel}</h3>
                <HealthMetricTiles metrics={health.data.metrics} keys={CORE_METRICS} />
              </>
            )}

            {sleep.data?.some((s) => s.total_sleep_s != null) && (
              <ChartFullscreen as="h3" title={`Sleep duration — over ${periodLabel}`}>
                <SleepDurationChart
                  data={eachDate(start, end).map((d) => {
                    const session = sleep.data?.find((s) => s.local_date === d);
                    return {
                      x: d,
                      hours:
                        session?.total_sleep_s != null
                          ? Math.round((session.total_sleep_s / 3600) * 10) / 10
                          : null,
                    };
                  })}
                  tickFormatter={formatMonthDayTick}
                  interval={2}
                />
              </ChartFullscreen>
            )}

            {anyMetricHasData(health.data.metrics, HRV_METRIC) && (
              <ChartFullscreen as="h3" title={`HRV — over ${periodLabel}`}>
                <HealthTrendChart metrics={health.data.metrics} keys={HRV_METRIC} />
              </ChartFullscreen>
            )}

            {anyMetricHasData(health.data.metrics, WEIGHT_METRIC) && (
              <ChartFullscreen as="h3" title={`Weight — over ${periodLabel}`}>
                <HealthTrendChart metrics={health.data.metrics} keys={WEIGHT_METRIC} />
              </ChartFullscreen>
            )}
          </>
        )}
      </section>

      <table className="month-grid">
        <thead>
          <tr>
            {weekdayLabels(weekStartDay).map((label) => (
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
                  const planned = plannedByDate.get(date);
                  const races = racesByDate.get(date);
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
                            ` · ${metersToDisplay(day.activity_distance_m).toFixed(1)}${unitLabel}`}
                          {day.activity_moving_duration_s != null &&
                            ` · ${(day.activity_moving_duration_s / 3600).toFixed(1)}h`}
                        </div>
                      )}
                      {planned?.map((p) => (
                        <div key={p.id} className="month-grid__planned">
                          <Icon name={plannedWorkoutSportStyle(p.sport).icon} />
                          {p.scheduled_time && `${formatHHMM(p.scheduled_time)} `}
                          {p.name || p.sport}
                        </div>
                      ))}
                      {races?.map((r) => (
                        <div key={r.id} className="month-grid__race">
                          <Icon name="trophy" />
                          {r.name}
                        </div>
                      ))}
                    </td>
                  );
                })}
                <td>
                  {weekRollup && weekRollup.activity_count > 0 && (
                    <Link to={`/calendar/week/${week[0]}`}>
                      {weekRollup.activity_count} act
                      {weekRollup.activity_distance_m != null &&
                        ` · ${metersToDisplay(weekRollup.activity_distance_m).toFixed(1)} ${unitLabel}`}
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
          <h3>Planned workout</h3>
          <ScheduleWorkoutForm localDate={expandedDate} />
          <h3>Race</h3>
          <Suspense fallback={<LoadingSpinner size="sm" />}>
            <PlannedRaceForm localDate={expandedDate} />
          </Suspense>
          <NotesPanel entityType="day" entityId={expandedDate} />
        </section>
      )}
    </main>
  );
}
