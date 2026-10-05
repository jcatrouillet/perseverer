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
import { LoadingSpinner } from "../../components/LoadingSpinner";
import { PeriodStatsCard } from "../../components/PeriodStatsCard";
import { PeriodShareButton } from "../../components/ShareButton";
import { RunningStats } from "../../components/RunningStats";
import { SleepDurationChart } from "../../components/SleepDurationChart";
import { EARLIEST_PLAUSIBLE_DATE, localIsoDate } from "../../dateUtils";
import { anyMetricHasData, monthlyAverageSleepHours } from "../../healthStats";
import { CORE_METRICS, HRV_METRIC, WEIGHT_METRIC } from "../HealthPage";
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
  const today = localIsoDate();
  const start = dates[0] ?? today;
  const end = dates[dates.length - 1] ?? today;

  const fitness = useFitness(start, end);
  // Deliberately NOT bounded to `start` (the earliest *activity*) -- health data can genuinely
  // predate the athlete's first tracked activity (e.g. Apple Health weight history reaching back
  // to 2011 for an athlete whose first tracked run was 2016), and GET /health/dashboard only
  // ever returns days that actually have data, so widening the query costs nothing when that
  // earlier history doesn't exist. EARLIEST_PLAUSIBLE_DATE is the same "genuinely earliest
  // anything could be" floor `all` above already filters activities against.
  const health = useHealthDashboard(EARLIEST_PLAUSIBLE_DATE, end);
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
      <PeriodShareButton periodType="all" />
      {allActivities.isLoading && <LoadingSpinner />}
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
                <h3>Core daily summary — average over all time</h3>
                <HealthMetricTiles metrics={health.data.metrics} keys={CORE_METRICS} />
              </>
            )}

            {monthlySleep.some((p) => p.avgHours != null) && (
              <ChartFullscreen as="h3" title="Average monthly sleep — over all time">
                <SleepDurationChart
                  data={monthlySleep.map((p) => ({ x: p.month, hours: p.avgHours }))}
                  tickFormatter={formatMonthTick}
                  interval={Math.max(0, Math.ceil(monthlySleep.length / 10) - 1)}
                />
              </ChartFullscreen>
            )}

            {anyMetricHasData(health.data.metrics, HRV_METRIC) && (
              <ChartFullscreen as="h3" title="HRV — over all time">
                <HealthTrendChart
                  metrics={health.data.metrics}
                  keys={HRV_METRIC}
                  tickGranularity="year"
                />
              </ChartFullscreen>
            )}

            {anyMetricHasData(health.data.metrics, WEIGHT_METRIC) && (
              <ChartFullscreen as="h3" title="Weight — over all time">
                <HealthTrendChart
                  metrics={health.data.metrics}
                  keys={WEIGHT_METRIC}
                  tickGranularity="month"
                />
              </ChartFullscreen>
            )}
          </>
        )}
      </section>
    </main>
  );
}
