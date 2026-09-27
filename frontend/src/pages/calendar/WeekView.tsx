import { useLocation } from "wouter";

import {
  useActivities,
  useAllActivities,
  useCalendar,
  useClimbingSummary,
  useFitness,
  useHealthDashboard,
  usePlannedRacesForDate,
  usePlannedWorkoutsForDate,
  usePlannedWorkoutsList,
  useSleep,
  useWeatherForecast,
} from "../../api/queries";
import type {
  ActivitySummary,
  DayRollupOut,
  ForecastDayOut,
  PlannedWorkoutListItemOut,
} from "../../api/types";
import { ActivityCard } from "../../components/ActivityCard";
import { GoalButton } from "../../components/GoalButton";
import { DateNavigator } from "../../components/DateNavigator";
import { ClimbingStatsCard } from "../../components/ClimbingStatsCard";
import { HikeStatsCard } from "../../components/HikeStatsCard";
import { Icon } from "../../components/Icon";
import { LoadingSpinner } from "../../components/LoadingSpinner";
import { NotesPanel } from "../../components/NotesPanel";
import { MetricChip, StatTile } from "../../components/StatTile";
import { WeekRunningStats } from "../../components/WeekRunningStats";
import { WeekWellnessCharts } from "../../components/WeekWellnessCharts";
import { WorkoutLoadBar } from "../../components/WorkoutLoadBar";
import { eachDate, isoDate, parseIsoDate, sumDayRollups, weekRange } from "../../dateUtils";
import { useDistanceFormat } from "../../formatDistance";
import { useTimeFormat } from "../../formatTime";
import { plannedWorkoutSportStyle } from "../../metricStyle";
import { usePersonalize } from "../../PersonalizeContext";
import { formatDurationHM, personalRecords } from "../../runningStats";
import { weatherCodeInfo } from "../../weatherCode";
import { groupByLocalDate } from "../../yearStats";
import "../../styles/activity-list.css";
import "../../styles/calendar.css";
import "../../styles/plannedWorkout.css";

const RUNNING_HISTORY_WEEKS = 52;

function formatDayHeading(localDate: string): { date: string; weekday: string } {
  const d = new Date(`${localDate}T00:00:00Z`);
  const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const weekday = d.toLocaleDateString("en-US", { weekday: "long", timeZone: "UTC" });
  return { date, weekday };
}

// planned_workout.sport is always one of these five exact strings (never the recorded-activity
// taxonomy's own sport/sub_sport pair, e.g. yoga is sport="training"/sub_sport="yoga" once
// actually recorded) -- same small duplicated label map ScheduleWorkoutForm.tsx's own (private)
// SPORTS array already carries, duplicated here rather than exported/imported since it's a
// three-line constant, not shared logic.
const PLANNED_SPORT_LABELS: Record<string, string> = {
  running: "Running",
  yoga: "Yoga",
  bouldering: "Bouldering",
  hiit: "HIIT",
  strength_training: "Strength training",
};

function plannedSportLabel(sport: string): string {
  return PLANNED_SPORT_LABELS[sport] ?? sport;
}

interface SportCompliance {
  sport: string;
  completed: number;
  scheduled: number;
  pct: number;
}

// Compliance is deliberately count-based (completed workouts / scheduled workouts), not
// distance- or load-weighted -- count is the one signal every sport tier carries identically --
// running's own estimated_distance_m/estimated_load are null for yoga/bouldering/hiit/
// strength_training (planned_workout_stats.py is running-only), so a distance- or load-weighted
// number couldn't be computed for four of the five sport tiers anyway. "Done" is
// `completed_at != null` (the athlete's own explicit marker) OR `matched_activity_id != null`
// (a same-day, matching-sport recorded activity, computed server-side at read time -- see
// PlannedWorkoutListItemOut's own docstring) -- a workout a Garmin sync already confirms counts
// as done without a separate manual click. Scoped to `local_date <= today`: a workout scheduled
// later this week hasn't happened yet, so counting it as "not done" would understate a week
// that's still in progress rather than reflect anything the athlete actually missed. A fully
// future week (every date > today) naturally produces an empty list, needing no separate case.
function computeCompliance(
  workouts: PlannedWorkoutListItemOut[],
  today: string,
): SportCompliance[] {
  const bySport = new Map<string, { completed: number; scheduled: number }>();
  for (const w of workouts) {
    if (w.local_date > today) continue;
    const entry = bySport.get(w.sport) ?? { completed: 0, scheduled: 0 };
    entry.scheduled += 1;
    if (w.completed_at != null || w.matched_activity_id != null) entry.completed += 1;
    bySport.set(w.sport, entry);
  }
  return [...bySport.entries()]
    .map(([sport, { completed, scheduled }]) => ({
      sport,
      completed,
      scheduled,
      pct: Math.round((completed / scheduled) * 100),
    }))
    .sort((a, b) => b.scheduled - a.scheduled || a.sport.localeCompare(b.sport));
}

// One `usePlannedWorkoutsForDate` call per day, each in its own component instance -- a week has
// 7 dates and this hook only takes one date at a time (no bulk-range full-detail endpoint
// exists, only the summary-list one MonthView's grid cell uses), so a small per-day component is
// what lets each day fetch its own date without breaking the Rules of Hooks inside WeekView's
// own single render. Read-only (icon + WorkoutLoadBar only, no Edit/Delete/Push) -- Week view
// isn't an editing surface, Day/Month view already are.
function WeekDayPlannedWorkouts({ date }: { date: string }) {
  const workouts = usePlannedWorkoutsForDate(date);
  const { formatHHMM } = useTimeFormat();
  if (!workouts.data || workouts.data.length === 0) return null;
  return (
    <div className="activity-day-group__planned">
      {workouts.data.map((w) => (
        <div key={w.id}>
          <div className="month-grid__planned">
            {w.sport != null && <Icon name={plannedWorkoutSportStyle(w.sport).icon} />}
            {w.scheduled_time && `${formatHHMM(w.scheduled_time)} `}
            {w.name || w.sport}
          </div>
          <WorkoutLoadBar workout={w} />
        </div>
      ))}
    </div>
  );
}

// Same "one hook call per day, own component instance" pattern as WeekDayPlannedWorkouts above,
// for the same Rules-of-Hooks reason -- races are a separate small table, not a planned_workout
// sport tier. Read-only here too (Edit/Delete live on Day/Month view).
function WeekDayRaces({ date }: { date: string }) {
  const races = usePlannedRacesForDate(date);
  const { formatHHMM } = useTimeFormat();
  if (!races.data || races.data.length === 0) return null;
  return (
    <div className="activity-day-group__planned">
      {races.data.map((r) => (
        <div key={r.id} className="month-grid__race">
          <Icon name="trophy" />
          {r.scheduled_time && `${formatHHMM(r.scheduled_time)} `}
          {r.name}
        </div>
      ))}
    </div>
  );
}

// One column per weekday -- past/current days show that day's recorded activities (ActivityCard,
// same component the activity list and day view use), future days show the scheduled workout's
// load bar instead; both simply render from whatever data exists for that date rather than
// branching on past-vs-future, since a future date naturally has no activities yet and a fully
// past date rarely still has an undone scheduled workout. The whole column navigates to that
// day's own /day/:date view on click (setLocation, not a wrapping <Link> -- ActivityCard already
// renders its own <a> per activity, and nesting an anchor inside another anchor is invalid HTML/
// gets silently mangled by the browser's own parser); the activities list stops that click from
// bubbling so tapping a specific activity opens *that activity*, not the day, which is the more
// specific and therefore more useful destination.
function WeekDayColumn({
  date,
  day,
  activities,
  forecast,
  steps,
  isToday,
  onNavigate,
}: {
  date: string;
  day: DayRollupOut | undefined;
  activities: ActivitySummary[];
  forecast: ForecastDayOut | undefined;
  steps: number | null | undefined;
  isToday: boolean;
  onNavigate: (date: string) => void;
}) {
  const { date: dateLabel, weekday } = formatDayHeading(date);
  const forecastInfo = forecast ? weatherCodeInfo(forecast.weather_code) : null;
  return (
    <div
      className={`week-columns__day${isToday ? " week-columns__day--today" : ""}`}
      onClick={() => onNavigate(date)}
    >
      <div className="week-columns__header">
        <span>
          <span className="week-columns__weekday">{weekday.slice(0, 3)}</span>{" "}
          <span className="week-columns__date">{dateLabel}</span>
        </span>
        {day?.sleep_total_s != null && (
          <MetricChip
            label={`${(day.sleep_total_s / 3600).toFixed(1)}h`}
            icon="moon"
            tone="cadence"
          />
        )}
      </div>
      {forecast && forecastInfo && (
        <div className="week-columns__forecast" title={forecastInfo.label}>
          <Icon name={forecastInfo.icon} />
          <span>
            {Math.round(forecast.temperature_min_c)}–{Math.round(forecast.temperature_max_c)}°
          </span>
        </div>
      )}
      {steps != null && (
        <div className="week-columns__steps" title="Steps">
          <Icon name="steps" />
          <span>{Math.round(steps).toLocaleString()}</span>
        </div>
      )}
      <WeekDayPlannedWorkouts date={date} />
      <WeekDayRaces date={date} />
      {activities.length > 0 && (
        <div className="week-columns__activities" onClick={(e) => e.stopPropagation()}>
          {activities.map((activity) => (
            <ActivityCard key={activity.id} activity={activity} />
          ))}
        </div>
      )}
    </div>
  );
}

export function WeekView({ date }: { date: string }) {
  const { week_start_day: weekStartDay } = usePersonalize();
  const { metersToDisplay, unitLabel } = useDistanceFormat();
  const { start, end } = weekRange(date, weekStartDay);
  const priorWeekStartDate = parseIsoDate(start);
  priorWeekStartDate.setUTCDate(priorWeekStartDate.getUTCDate() - 7);
  const priorWeekStart = isoDate(priorWeekStartDate);
  const priorWeekEndDate = parseIsoDate(start);
  priorWeekEndDate.setUTCDate(priorWeekEndDate.getUTCDate() - 1);
  const priorWeekEnd = isoDate(priorWeekEndDate);
  const runningRangeStartDate = parseIsoDate(start);
  runningRangeStartDate.setUTCDate(runningRangeStartDate.getUTCDate() - RUNNING_HISTORY_WEEKS * 7);
  const runningRangeStart = isoDate(runningRangeStartDate);

  // Spans the prior week too (one call, not two) -- "Week stats" and "this week vs last week"
  // are both summed client-side from these same per-day rows (sumDayRollups above) rather than
  // read from the Monday-keyed period_rollup, so the totals stay correct for any weekStartDay.
  const calendar = useCalendar(priorWeekStart, end);
  const activities = useActivities({ startDate: start, endDate: end, limit: 50 });
  const runningHistory = useAllActivities({
    sport: "running",
    startDate: runningRangeStart,
    endDate: end,
  });
  // Unbounded (distinct from `runningHistory`'s 52-week window) so the "PBs set this week"
  // callout can tell a genuine all-time PR apart from merely a strong trailing-year effort --
  // see ADR 0011 decision 3 and RunningStats.tsx's allTimeRecords prop docstring.
  const allTimeRunning = useAllActivities({ sport: "running" });
  // Also spans the prior week, for the ramp (week-over-week CTL change) comparison below.
  const fitness = useFitness(priorWeekStart, end);
  const health = useHealthDashboard(start, end);
  const sleep = useSleep(start, end);
  const climbing = useClimbingSummary(start, end);
  // One fetch for the whole week, not per-day (unlike WeekDayPlannedWorkouts/WeekDayRaces above)
  // -- Open-Meteo's forecast is naturally a single call regardless of how many of its returned
  // days fall inside this particular week, so there's no Rules-of-Hooks reason to split it per
  // column. Keyed by local_date; a date with no matching entry (past days, or days beyond
  // Open-Meteo's own forecast horizon) simply renders no forecast row.
  const forecast = useWeatherForecast();
  const forecastByDate = new Map((forecast.data?.days ?? []).map((d) => [d.local_date, d]));
  // Steps is a summed-per-day metric (same LOGICAL_METRICS["steps"] alias-merge GET
  // /health/dashboard already resolves, e.g. garmin.daily_summary.totalSteps) -- reuses the same
  // `health` fetch WeekWellnessCharts already draws its own metrics from, no second call.
  const stepsMetric = health.data?.metrics.find((m) => m.logical_metric === "steps");
  const stepsByDate = new Map(
    (stepsMetric?.daily ?? []).map((d) => [d.local_date, d.value_sum]),
  );
  // One bulk fetch for the whole week (the summary-list endpoint MonthView's own grid cell
  // already uses), not the per-day usePlannedWorkoutsForDate hook WeekDayPlannedWorkouts calls
  // above -- compliance needs every day's own scheduled workouts at once to aggregate by sport,
  // and the summary shape (sport + completed_at, no steps/estimates) is all it needs.
  const plannedWorkoutsThisWeek = usePlannedWorkoutsList(start, end);
  const [, setLocation] = useLocation();
  // Deliberately the browser's own local calendar date, not `isoDate(new Date())`'s UTC
  // conversion (the pattern used elsewhere in this app for a coarse "today" default) -- that
  // conversion already rolls over to the next day mid-evening for anyone west of UTC (e.g.
  // 11pm Pacific is already the next UTC date), which would highlight tomorrow's column as
  // "today" for the exact hours an athlete is most likely to be looking at their own week.
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const compliance = computeCompliance(plannedWorkoutsThisWeek.data ?? [], today);
  const weekDates = new Set(eachDate(start, end));
  const priorWeekDates = new Set(eachDate(priorWeekStart, priorWeekEnd));
  const weekTotal = calendar.data
    ? sumDayRollups(calendar.data.days.filter((d) => weekDates.has(d.local_date)))
    : undefined;
  const priorWeekTotal = calendar.data
    ? sumDayRollups(calendar.data.days.filter((d) => priorWeekDates.has(d.local_date)))
    : undefined;
  const monthOfWeekStart = start.slice(0, 7); // YYYY-MM
  // Week-over-week comparison: always the immediately preceding calendar week (not "this week
  // last year" -- week numbers don't align cleanly across years the way months/years do), shown
  // as that week's own total distance rather than a +/- delta (the user found a bare delta
  // disconnected from the number it was relative to -- see PeriodStatsCard's compareMeta).
  const priorWeekMeta =
    priorWeekTotal && priorWeekTotal.activity_distance_m > 0
      ? `${metersToDisplay(priorWeekTotal.activity_distance_m).toFixed(1)}${unitLabel} previous week`
      : null;

  const activitiesByDate = new Map(
    groupByLocalDate(activities.data?.items ?? []).map((g) => [g.localDate, g.activities]),
  );
  const hikes = (activities.data?.items ?? []).filter((a) => a.sport === "hiking");

  // Training load/fitness/fatigue/form for the week: the week's own summed daily training load,
  // and CTL/ATL/TSB as of the week's last day (its cumulative fitness/fatigue/form state, not an
  // average across the week -- matching how CTL/ATL/TSB are read everywhere else in this app,
  // e.g. the day view's own Fitness & Form card). Ramp is the week-over-week CTL change: this
  // week's ending fitness minus last week's, the same "ramp rate" concept TrainingPeaks/
  // intervals.icu use to flag a too-fast fitness increase.
  const weekLoad = (fitness.data ?? [])
    .filter((f) => f.local_date >= start && f.local_date <= end)
    .reduce((sum, f) => sum + f.training_load, 0);
  const weekEndFitness = fitness.data?.find((f) => f.local_date === end);
  const priorWeekEndFitness = fitness.data?.find((f) => f.local_date === priorWeekEnd);
  const ctlRamp =
    weekEndFitness && priorWeekEndFitness ? weekEndFitness.ctl - priorWeekEndFitness.ctl : null;

  return (
    <main>
      <DateNavigator
        year={Number(monthOfWeekStart.slice(0, 4))}
        month={Number(monthOfWeekStart.slice(5, 7))}
        selectedWeekStart={start}
      />
      <div className="calendar-header">
        <h1>
          Week of {start} – {end}
        </h1>
        <GoalButton periodType="week" periodStart={start} periodLabel={`Week of ${start}`} />
      </div>

      <section className="card">
        <h2>Notes</h2>
        <NotesPanel entityType="week" entityId={start} showHeading={false} />
      </section>

      {calendar.isLoading && <LoadingSpinner />}
      {calendar.isError && <p role="alert">Could not load the week.</p>}

      <div className="week-columns">
        {eachDate(start, end).map((d) => (
          <WeekDayColumn
            key={d}
            date={d}
            day={calendar.data?.days.find((x) => x.local_date === d)}
            activities={activitiesByDate.get(d) ?? []}
            forecast={forecastByDate.get(d)}
            steps={stepsByDate.get(d)}
            isToday={d === today}
            onNavigate={(navDate) => setLocation(`/day/${navDate}`)}
          />
        ))}
      </div>

      {weekTotal && weekTotal.activity_count > 0 && (
        <section className="card">
          <h2>Week stats</h2>
          <div className="stat-grid">
            <StatTile
              label="Total distance"
              value={metersToDisplay(weekTotal.activity_distance_m ?? 0).toFixed(1)}
              unit={unitLabel}
              meta={priorWeekMeta}
              icon="route"
              tone="pace"
              hero
            />
            <StatTile
              label="Total time"
              value={formatDurationHM(
                weekTotal.activity_moving_duration_s ?? weekTotal.activity_duration_s ?? 0,
              )}
              icon="clock"
              tone="cadence"
              hero
            />
            <StatTile
              label="Active days"
              value={weekTotal.activity_days_count}
              unit="of 7"
              icon="calendar"
              tone="elevation"
              hero
            />
            <StatTile label="Activities" value={weekTotal.activity_count} icon="calendar" tone="pace" />
            {weekTotal.activity_elevation_gain_m != null && (
              <StatTile
                label="Total elevation"
                value={weekTotal.activity_elevation_gain_m.toFixed(0)}
                unit="m"
                icon="mountain"
                tone="elevation"
              />
            )}
            {weekLoad > 0 && (
              <StatTile label="Load" value={Math.round(weekLoad)} icon="bolt" tone="load" />
            )}
            {weekEndFitness && (
              <>
                <StatTile
                  label="Fitness (CTL)"
                  value={weekEndFitness.ctl.toFixed(1)}
                  icon="trend"
                  tone="elevation"
                />
                <StatTile
                  label="Fatigue (ATL)"
                  value={weekEndFitness.atl.toFixed(1)}
                  icon="bolt"
                  tone="load"
                />
                <StatTile
                  label="Form (TSB)"
                  value={weekEndFitness.tsb.toFixed(1)}
                  icon="gauge"
                  tone="pace"
                />
              </>
            )}
            {ctlRamp != null && (
              <StatTile
                label="Ramp"
                value={`${ctlRamp >= 0 ? "+" : ""}${ctlRamp.toFixed(1)}`}
                meta="CTL change vs last week"
                icon="trend"
                tone="elevation"
              />
            )}
          </div>
        </section>
      )}

      {compliance.length > 0 && (
        <section className="card">
          <h2>Compliance</h2>
          <div className="stat-grid">
            {compliance.map((c) => (
              <StatTile
                key={c.sport}
                label={`${plannedSportLabel(c.sport)} compliance`}
                value={`${c.pct}%`}
                meta={`${c.completed} of ${c.scheduled} done`}
                icon={plannedWorkoutSportStyle(c.sport).icon}
                tone={plannedWorkoutSportStyle(c.sport).tone}
              />
            ))}
          </div>
        </section>
      )}

      {runningHistory.data && (
        <WeekRunningStats
          runningActivities={runningHistory.data}
          rangeStart={runningRangeStart}
          rangeEnd={end}
          weekStart={start}
          weekEnd={end}
          priorWeekStart={priorWeekStart}
          priorWeekEnd={priorWeekEnd}
          allTimeRecords={personalRecords(allTimeRunning.data ?? [])}
        />
      )}

      <HikeStatsCard activities={hikes} />
      <ClimbingStatsCard summary={climbing.data} />

      {health.data && (
        <WeekWellnessCharts
          metrics={health.data.metrics}
          sleepSessions={sleep.data ?? []}
          weekStart={start}
          weekEnd={end}
        />
      )}
    </main>
  );
}
