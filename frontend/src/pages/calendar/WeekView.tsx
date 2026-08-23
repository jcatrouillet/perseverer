import { useState } from "react";

import {
  useActivities,
  useAllActivities,
  useCalendar,
  useCalendarWeeks,
  useClimbingSummary,
  useFitness,
  useHealthDashboard,
  useSleep,
} from "../../api/queries";
import { ActivityCard } from "../../components/ActivityCard";
import { DateNavigator } from "../../components/DateNavigator";
import { ClimbingStatsCard } from "../../components/ClimbingStatsCard";
import { HikeStatsCard } from "../../components/HikeStatsCard";
import { NotesPanel } from "../../components/NotesPanel";
import { MetricChip, StatTile } from "../../components/StatTile";
import { WeekRunningStats } from "../../components/WeekRunningStats";
import { WeekWellnessCharts } from "../../components/WeekWellnessCharts";
import { eachDate, isoDate, parseIsoDate, weekRange } from "../../dateUtils";
import { formatDurationHM, personalRecords } from "../../runningStats";
import { groupByLocalDate } from "../../yearStats";
import "../../styles/activity-list.css";
import "../../styles/calendar.css";

const RUNNING_HISTORY_WEEKS = 52;

function formatDayHeading(localDate: string): { date: string; weekday: string } {
  const d = new Date(`${localDate}T00:00:00Z`);
  const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const weekday = d.toLocaleDateString("en-US", { weekday: "long", timeZone: "UTC" });
  return { date, weekday };
}

// This got left behind when Milestones B/D built ActivityCard-based day groups for the
// activity list and day view -- WeekView only ever picked up the new DateNavigator (Milestone
// A) and kept its original plain Phase-6 day list otherwise. Rebuilt on the same
// `.activity-day-group` + ActivityCard pattern those two views already established, so a week
// reads as seven real day groups (with rest days visibly empty) rather than one text summary
// line per day.
export function WeekView({ date }: { date: string }) {
  const { start, end } = weekRange(date);
  const priorWeekStartDate = parseIsoDate(start);
  priorWeekStartDate.setUTCDate(priorWeekStartDate.getUTCDate() - 7);
  const priorWeekStart = isoDate(priorWeekStartDate);
  const priorWeekEndDate = parseIsoDate(start);
  priorWeekEndDate.setUTCDate(priorWeekEndDate.getUTCDate() - 1);
  const priorWeekEnd = isoDate(priorWeekEndDate);
  const runningRangeStartDate = parseIsoDate(start);
  runningRangeStartDate.setUTCDate(runningRangeStartDate.getUTCDate() - RUNNING_HISTORY_WEEKS * 7);
  const runningRangeStart = isoDate(runningRangeStartDate);

  const calendar = useCalendar(start, end);
  // Spans the prior week too, so "this week vs last week" doesn't need a second endpoint call.
  const weeks = useCalendarWeeks(priorWeekStart, end);
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
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  const weekTotal = weeks.data?.periods.find((p) => p.period_start === start);
  const priorWeekTotal = weeks.data?.periods.find((p) => p.period_start === priorWeekStart);
  const monthOfWeekStart = start.slice(0, 7); // YYYY-MM
  // Week-over-week comparison: always the immediately preceding calendar week (not "this week
  // last year" -- week numbers don't align cleanly across years the way months/years do), shown
  // as that week's own total distance rather than a +/- delta (the user found a bare delta
  // disconnected from the number it was relative to -- see PeriodStatsCard's compareMeta).
  const priorWeekMeta =
    priorWeekTotal?.activity_distance_m != null
      ? `${(priorWeekTotal.activity_distance_m / 1000).toFixed(1)}km previous week`
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
      <h1>
        Week of {start} – {end}
      </h1>

      {weekTotal && weekTotal.activity_count > 0 && (
        <section className="card">
          <h2>Week stats</h2>
          <div className="stat-grid">
            <StatTile
              label="Total distance"
              value={((weekTotal.activity_distance_m ?? 0) / 1000).toFixed(1)}
              unit="km"
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

      {calendar.isLoading && <p>Loading…</p>}
      {calendar.isError && <p role="alert">Could not load the week.</p>}

      {eachDate(start, end).map((d) => {
        const day = calendar.data?.days.find((x) => x.local_date === d);
        const { date: dateLabel, weekday } = formatDayHeading(d);
        const dayActivities = activitiesByDate.get(d) ?? [];
        return (
          <section className="activity-day-group" key={d}>
            <div className="activity-day-group__header">
              <span className="activity-day-group__date">{dateLabel}</span>
              <span className="activity-day-group__weekday">{weekday}</span>
              <div className="activity-day-group__wellness">
                {day?.sleep_total_s != null && (
                  <MetricChip
                    label={`${(day.sleep_total_s / 3600).toFixed(1)}h sleep`}
                    icon="moon"
                    tone="cadence"
                  />
                )}
                <button
                  type="button"
                  className="button"
                  onClick={() => setExpandedDate(expandedDate === d ? null : d)}
                >
                  Notes
                </button>
              </div>
            </div>
            {dayActivities.length > 0 && (
              <div className="activity-day-group__list">
                {dayActivities.map((activity) => (
                  <ActivityCard key={activity.id} activity={activity} iconSize="large" />
                ))}
              </div>
            )}
            {expandedDate === d && <NotesPanel entityType="day" entityId={d} />}
          </section>
        );
      })}
    </main>
  );
}
