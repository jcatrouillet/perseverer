// The Month/Year/All-time summaries' Cycling section: the Running section's counterpart for rides
// (RunningStats.tsx), built from the same helpers over rides only. Rendered only when the period
// has at least one ride. Records are the fastest whole ride near each distance (5, 10, 20, 40 and
// 100 km), the same approximation as the running records.
import { Link, useLocation } from "wouter";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ActivitySummary } from "../api/types";
import { averageSpeedKmh, powerVsDistance, trailingDistance } from "../cyclingStats";
import { monthName, parseIsoDate } from "../dateUtils";
import { useDistanceFormat } from "../formatDistance";
import { toneColor } from "../metricStyle";
import type { DistanceBucket } from "../runningStats";
import {
  CYCLING_PR_DISTANCES,
  distanceByDay,
  distanceByYear,
  distinctActiveDates,
  formatDurationHM,
  longestStreakAndBreak,
  monthlyDistanceM,
  newAllTimePrs,
  personalRecords,
  weekdayLabel,
  weekdayStats,
} from "../runningStats";
import { ChartFullscreen } from "./ChartFullscreen";
import { Icon } from "./Icon";
import { SportSectionTitle } from "./SportSectionTitle";
import { StatTile } from "./StatTile";
import "../styles/running-stats.css";

const RIDE = toneColor("ride");
const POWER = toneColor("power");
const MS_PER_DAY = 86_400_000;
const TOOLTIP_STYLE = {
  background: "var(--color-surface-raised)",
  border: "1px solid var(--color-border)",
};

function formatShortDate(iso: string): string {
  return parseIsoDate(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

function formatDateRange(start: string | null, end: string | null): string | null {
  if (!start || !end) return null;
  return start === end
    ? formatShortDate(start)
    : `${formatShortDate(start)} – ${formatShortDate(end)}`;
}

export function CyclingStats({
  rides,
  allRides,
  startDate,
  endDate,
  periodLabel,
  trailingWindowDays,
}: {
  /** The period's rides. The section isn't rendered when this is empty. */
  rides: ActivitySummary[];
  /** Every ride ever: the trailing windows reach back before the period, and a record is marked
   * as an all-time best when it also holds over the whole history. */
  allRides: ActivitySummary[];
  startDate: string;
  endDate: string;
  periodLabel: string;
  /** The trailing chart's window: 7 days for a month, 30 for a year, 365 for all time. */
  trailingWindowDays: number;
}) {
  const [, setLocation] = useLocation();
  const { metersToDisplay, unitLabel, kmhToDisplay, speedUnitLabel } = useDistanceFormat();
  if (rides.length === 0) return null;

  const spanDays =
    Math.round((parseIsoDate(endDate).getTime() - parseIsoDate(startDate).getTime()) / MS_PER_DAY) +
    1;
  const useDailyBuckets = spanDays <= 31;
  const useYearRows = spanDays > 366;

  const totalDistanceM = rides.reduce((sum, a) => sum + (a.distance_m ?? 0), 0);
  const speed = averageSpeedKmh(rides);
  const longest = rides.reduce((max, a) => ((a.distance_m ?? 0) > (max.distance_m ?? 0) ? a : max));
  const maxHrRide = rides.reduce<ActivitySummary | null>((best, a) => {
    if (a.max_hr_bpm == null) return best;
    return best == null || a.max_hr_bpm > best.max_hr_bpm! ? a : best;
  }, null);
  const activeDates = distinctActiveDates(rides);
  const streaks = longestStreakAndBreak(activeDates);
  const weekdays = weekdayStats(rides);
  const mostOften = weekdays.reduce((max, d) => (d.count > max.count ? d : max));
  const leastOften = weekdays.reduce((min, d) => (d.count < min.count ? d : min));
  const firstDay = parseIsoDate(activeDates[0]!).getTime();
  const lastDay = parseIsoDate(activeDates[activeDates.length - 1]!).getTime();
  const weeksElapsed = Math.max(1, ((lastDay - firstDay) / MS_PER_DAY + 1) / 7);

  const bucketData: DistanceBucket[] = useDailyBuckets
    ? distanceByDay(rides, startDate, endDate)
    : useYearRows
      ? distanceByYear(rides, startDate, endDate)
      : monthlyDistanceM(rides).map((m, i) => ({
          label: monthName(i + 1).slice(0, 3),
          km: Math.round(m / 100) / 10,
        }));
  const bucketTitle = useDailyBuckets
    ? "Distance per day"
    : useYearRows
      ? "Distance per year"
      : "Distance per month";

  const trailing = trailingDistance(allRides, startDate, endDate, trailingWindowDays);
  const trailingTicks = useDailyBuckets
    ? trailing.map((p) => p.local_date).filter((_, i) => i % 5 === 0)
    : useYearRows
      ? trailing.map((p) => p.local_date).filter((d) => d.endsWith("-01-01"))
      : trailing.map((p) => p.local_date).filter((d) => d.endsWith("-01"));
  const trailingTickFormatter = useDailyBuckets
    ? (iso: string) => iso.slice(8, 10)
    : useYearRows
      ? (iso: string) => iso.slice(0, 4)
      : (iso: string) => monthName(Number(iso.slice(5, 7))).slice(0, 3);

  const power = powerVsDistance(rides);
  const records = personalRecords(rides, CYCLING_PR_DISTANCES);
  const allTimeLabels = new Set(
    newAllTimePrs(records, personalRecords(allRides, CYCLING_PR_DISTANCES)).map((r) => r.label),
  );

  return (
    <section className="card running-stats">
      <SportSectionTitle sport="cycling">Cycling</SportSectionTitle>
      <div className="stat-grid">
        <StatTile
          label={unitLabel === "mi" ? "Miles ridden" : "Kilometers ridden"}
          value={metersToDisplay(totalDistanceM).toFixed(0)}
          unit={unitLabel}
          icon="route"
          tone="ride"
          hero
        />
        <StatTile label="Number of rides" value={rides.length} icon="bike" tone="load" hero />
        <StatTile
          label="Average speed"
          value={speed != null ? kmhToDisplay(speed).toFixed(1) : "—"}
          unit={speedUnitLabel}
          icon="gauge"
          tone="ride"
          hero
        />
        <Link href={`/activities/${longest.id}`} className="stat-tile-link">
          <StatTile
            label="Longest ride"
            value={metersToDisplay(longest.distance_m ?? 0).toFixed(1)}
            unit={unitLabel}
            meta={longest.local_date}
            icon="trophy"
            tone="load"
            hero
          />
        </Link>
        {maxHrRide && (
          <Link href={`/activities/${maxHrRide.id}`} className="stat-tile-link">
            <StatTile
              label="Max heart rate"
              value={Math.round(maxHrRide.max_hr_bpm!)}
              unit="bpm"
              meta={maxHrRide.local_date}
              icon="heart"
              tone="hr"
            />
          </Link>
        )}
        <StatTile
          label="Average ride length"
          value={metersToDisplay(totalDistanceM / rides.length).toFixed(1)}
          unit={unitLabel}
          icon="route"
          tone="ride"
        />
        <StatTile
          label="Average days ridden/week"
          value={(activeDates.length / weeksElapsed).toFixed(1)}
          icon="calendar"
          tone="elevation"
        />
        <StatTile
          label="Most often ridden on"
          value={weekdayLabel(mostOften.day)}
          icon="calendar"
          tone="elevation"
        />
        <StatTile
          label="Least often ridden on"
          value={weekdayLabel(leastOften.day)}
          icon="calendar"
          tone="neutral"
        />
        <StatTile
          label="Longest streak"
          value={streaks.longestStreakDays}
          unit={streaks.longestStreakDays === 1 ? "day" : "days"}
          meta={formatDateRange(streaks.longestStreakStart, streaks.longestStreakEnd)}
          icon="flame"
          tone="load"
        />
        <StatTile
          label="Longest break"
          value={streaks.longestBreakDays}
          unit={streaks.longestBreakDays === 1 ? "day" : "days"}
          meta={formatDateRange(streaks.longestBreakStart, streaks.longestBreakEnd)}
          icon="moon"
          tone="neutral"
        />
      </div>

      <div className="running-stats__charts">
        <div>
          <ChartFullscreen as="h3" title={bucketTitle}>
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={bucketData}>
                <CartesianGrid
                  stroke="var(--color-border)"
                  strokeDasharray="3 3"
                  vertical={false}
                />
                <XAxis
                  dataKey="label"
                  interval={useDailyBuckets ? Math.max(0, Math.ceil(bucketData.length / 8) - 1) : 0}
                  stroke="var(--color-text-muted)"
                  fontSize={11}
                />
                <YAxis stroke="var(--color-text-muted)" fontSize={11} width={32} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Bar dataKey="km" fill={RIDE} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </ChartFullscreen>
        </div>

        <div>
          <ChartFullscreen as="h3" title={`Trailing ${trailingWindowDays}-day kilometers`}>
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={trailing}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="local_date"
                  stroke="var(--color-text-muted)"
                  fontSize={11}
                  ticks={trailingTicks}
                  tickFormatter={trailingTickFormatter}
                />
                <YAxis stroke="var(--color-text-muted)" fontSize={11} width={32} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Line
                  type="monotone"
                  dataKey="km"
                  stroke={RIDE}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </ChartFullscreen>
        </div>

        <div>
          <ChartFullscreen as="h3" title="Average power vs distance">
            {power.length === 0 ? (
              <p className="chart-note">
                No ride in {periodLabel} recorded power (it needs a power meter or a smart trainer).
              </p>
            ) : (
              <ResponsiveContainer width="100%" height={180}>
                <ScatterChart>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="km"
                    type="number"
                    name="Distance"
                    unit=" km"
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                  />
                  <YAxis
                    dataKey="watts"
                    type="number"
                    name="Average power"
                    unit=" W"
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    width={52}
                    domain={["dataMin - 10", "dataMax + 10"]}
                  />
                  <Tooltip cursor={{ strokeDasharray: "3 3" }} contentStyle={TOOLTIP_STYLE} />
                  <Scatter
                    data={power}
                    fill={POWER}
                    isAnimationActive={false}
                    cursor="pointer"
                    onClick={(point: { payload?: { id: string } }) => {
                      if (point.payload) setLocation(`/activities/${point.payload.id}`);
                    }}
                  />
                </ScatterChart>
              </ResponsiveContainer>
            )}
          </ChartFullscreen>
        </div>
      </div>

      {records.length > 0 && (
        <div className="running-records">
          <h3>
            Records — {periodLabel}
            <span className="running-records__caveat">
              {" "}
              (fastest whole recorded ride near each distance, not a true best-effort segment)
            </span>
          </h3>
          <div className="table-scroll">
            <table className="running-records__table">
              <thead>
                <tr>
                  <th>Distance</th>
                  <th>Date</th>
                  <th>Speed</th>
                  <th>Distance ridden</th>
                  <th>Time</th>
                  <th>Rides</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r) => (
                  <tr
                    key={r.label}
                    className="running-records__row"
                    onClick={() => setLocation(`/activities/${r.activityId}`)}
                  >
                    <td>{r.label}</td>
                    <td>
                      {r.date}
                      {allTimeLabels.has(r.label) && (
                        <span className="running-records__pr-badge" title="All-time best">
                          <Icon name="trophy" />
                        </span>
                      )}
                    </td>
                    <td>
                      {kmhToDisplay(r.speedKmh).toFixed(1)} {speedUnitLabel}
                    </td>
                    <td>
                      {metersToDisplay(r.actualDistanceM).toFixed(1)} {unitLabel}
                    </td>
                    <td>{formatDurationHM(r.durationS)}</td>
                    <td>{r.eligibleCount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
