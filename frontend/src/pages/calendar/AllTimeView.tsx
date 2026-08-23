// The "All" link's destination -- same shape as YearView (period stats, Running, Fitness &
// Form, Health) but scoped to every activity ever recorded instead of one calendar year.
// Built entirely from useAllActivities (paginated GET /activities, see api/queries.ts) rather
// than a new backend endpoint, matching the client-aggregation pattern YearView/MonthView
// already use -- the one difference is pagination, since "all time" can exceed a single
// request's 500-row cap in a way a single year never does yet.
import {
  useAllActivities,
  useClimbingSummary,
  useFitness,
  useHealthDashboard,
  useSleep,
} from "../../api/queries";
import { ChartFullscreen } from "../../components/ChartFullscreen";
import { DateNavigator } from "../../components/DateNavigator";
import { FitnessChart } from "../../components/FitnessChart";
import { HealthMetricTiles } from "../../components/HealthMetricTiles";
import { HealthTrendChart } from "../../components/HealthTrendChart";
import { ClimbingStatsCard } from "../../components/ClimbingStatsCard";
import { HikeStatsCard } from "../../components/HikeStatsCard";
import { PeriodStatsCard } from "../../components/PeriodStatsCard";
import { RunningStats } from "../../components/RunningStats";
import { SleepDurationChart } from "../../components/SleepDurationChart";
import { EARLIEST_PLAUSIBLE_DATE, isoDate } from "../../dateUtils";
import { monthlyAverageSleepHours } from "../../healthStats";
import {
  BODY_COMPOSITION_ENERGY_METRICS,
  BODY_COMPOSITION_INDEX_METRICS,
  BODY_COMPOSITION_MASS_METRICS,
  BODY_COMPOSITION_PERCENT_METRICS,
  CORE_METRICS,
  HRV_SPO2_STRESS_METRICS,
} from "../HealthPage";
import { busiestYear } from "../../yearStats";
import "../../styles/calendar.css";

const SHORT_MONTH_NAMES = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

function formatMonthTick(yyyyMm: string): string {
  const [year, month] = yyyyMm.split("-");
  return `${SHORT_MONTH_NAMES[Number(month) - 1]} ${year}`;
}

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
  const sleep = useSleep(start, end);
  const climbing = useClimbingSummary(start, end);
  // Bounded to when sleep data actually starts, not the full activity history range above --
  // this athlete's activities go back to 2016 but sleep tracking (a wearable, arriving years
  // later) doesn't, so padding from `start` would produce years of leading empty months with
  // nothing to show.
  const sleepDates = (sleep.data ?? []).map((s) => s.local_date).sort();
  const monthlySleep =
    sleepDates.length > 0
      ? monthlyAverageSleepHours(
          sleep.data ?? [],
          sleepDates[0]!,
          sleepDates[sleepDates.length - 1]!,
        )
      : [];
  const runs = all.filter((a) => a.sport === "running");
  const hikes = all.filter((a) => a.sport === "hiking");
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

      <HikeStatsCard activities={hikes} />
      <ClimbingStatsCard summary={climbing.data} />

      <section className="card">
        {fitness.data ? (
          <ChartFullscreen as="h2" title="Fitness & Form">
            <FitnessChart series={fitness.data} />
          </ChartFullscreen>
        ) : (
          <h2>Fitness &amp; Form</h2>
        )}
        {fitness.isLoading && <p>Loading…</p>}
        {fitness.isError && <p role="alert">Could not load Fitness &amp; Form.</p>}
      </section>

      <section className="card">
        <h2>Health</h2>
        {health.isLoading && <p>Loading…</p>}
        {health.isError && <p role="alert">Could not load health data.</p>}
        {health.data && (
          <>
            <h3>Core daily summary — average over all time</h3>
            <HealthMetricTiles metrics={health.data.metrics} keys={CORE_METRICS} />

            <ChartFullscreen as="h3" title="Average monthly sleep — over all time">
              <SleepDurationChart
                data={monthlySleep.map((p) => ({ x: p.month, hours: p.avgHours }))}
                tickFormatter={formatMonthTick}
                interval={Math.max(0, Math.ceil(monthlySleep.length / 10) - 1)}
              />
            </ChartFullscreen>

            <ChartFullscreen as="h3" title="HRV / SpO2 / Stress — over all time">
              <HealthTrendChart metrics={health.data.metrics} keys={HRV_SPO2_STRESS_METRICS} />
            </ChartFullscreen>

            <ChartFullscreen as="h3" title="Weight & muscle mass — over all time">
              <HealthTrendChart
                metrics={health.data.metrics}
                keys={BODY_COMPOSITION_MASS_METRICS}
                tickGranularity="month"
              />
            </ChartFullscreen>

            <ChartFullscreen as="h3" title="Body composition % — over all time">
              <HealthTrendChart
                metrics={health.data.metrics}
                keys={BODY_COMPOSITION_PERCENT_METRICS}
                tickGranularity="month"
              />
            </ChartFullscreen>

            <ChartFullscreen
              as="h3"
              title="BMI, bone mass, visceral fat & metabolic age — over all time"
            >
              <HealthTrendChart
                metrics={health.data.metrics}
                keys={BODY_COMPOSITION_INDEX_METRICS}
                tickGranularity="month"
              />
            </ChartFullscreen>

            <ChartFullscreen as="h3" title="BMR — over all time">
              <HealthTrendChart
                metrics={health.data.metrics}
                keys={BODY_COMPOSITION_ENERGY_METRICS}
                tickGranularity="month"
              />
            </ChartFullscreen>
          </>
        )}
      </section>
    </main>
  );
}
