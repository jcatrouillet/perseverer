// A year-of-running summary, inspired by Strava/intervals.icu's own "year in sport" pages,
// built entirely from GET /activities (sport=running) -- no new backend endpoint (beyond
// exposing the already-stored utc_offset_s needed for local time-of-day). A stated yearly goal
// is deliberately left out: there's no goals feature/data model, and inventing one would mean
// fabricating a number rather than reporting a real one. Personal records are a documented
// approximation -- see personalRecords()'s own docstring in runningStats.ts.
import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ActivitySummary } from "../api/types";
import { ChartLegend } from "./ChartLegend";
import { Icon } from "./Icon";
import { StatTile } from "./StatTile";
import { isoDate, mondayOf, monthName, parseIsoDate } from "../dateUtils";
import type { DistanceBucket, PersonalRecord } from "../runningStats";
import {
  amPmCounts,
  DAILY_HEATMAP_SCALE,
  dailyStats,
  distanceByDay,
  distanceByYear,
  distinctActiveDates,
  effectiveDurationS,
  formatMinPerKm,
  formatPaceMinPerKm,
  isLongRun,
  longestStreakAndBreak,
  longRunPieDeg,
  monthlyDistanceM,
  nearestLegendKm,
  newAllTimePrs,
  personalRecords,
  rollingDistanceKm,
  scatterPointOpacities,
  shortRunHeatPct,
  WEEKLY_HEATMAP_SCALE,
  weekdayLabel,
  weekdayStats,
} from "../runningStats";
import "../styles/running-stats.css";

const MS_PER_DAY = 86_400_000;

function formatDuration(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.round((totalSeconds % 3600) / 60);
  return h > 0 ? `${h}:${m.toString().padStart(2, "0")}:00` : `${m}:00`;
}

function formatShortDate(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

function formatDateRange(start: string | null, end: string | null): string | null {
  if (!start || !end) return null;
  return start === end ? formatShortDate(start) : `${formatShortDate(start)} – ${formatShortDate(end)}`;
}

function formatHeatmapDate(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

function inRange(date: string, startDate: string, endDate: string): boolean {
  return date >= startDate && date <= endDate;
}

/** True if `week` is the last week whose days (within [startDate, endDate]) fall in a given
 * calendar month -- i.e. the next week starts a new month. Used to draw a divider line between
 * months (a no-op for a single-month range, since there's never a following month to differ). */
function isMonthEndWeek(
  week: HeatmapDay[],
  nextWeek: HeatmapDay[] | undefined,
  startDate: string,
  endDate: string,
): boolean {
  if (!nextWeek) return false;
  const thisMonth = week.find((d) => inRange(d.date, startDate, endDate))?.date.slice(5, 7);
  const nextMonth = nextWeek.find((d) => inRange(d.date, startDate, endDate))?.date.slice(5, 7);
  return thisMonth != null && nextMonth != null && thisMonth !== nextMonth;
}

interface HeatmapDay {
  date: string;
  km: number;
  paceMinPerKm: number | null;
  elevationM: number;
}

interface HeatmapWeek {
  weekStart: string;
  km: number;
  paceMinPerKm: number | null;
  elevationM: number;
}

function formatWeekLabel(weekStart: string): string {
  return `Week of ${formatShortDate(weekStart)}`;
}

export function RunningStats({
  activities,
  startDate,
  endDate,
  periodLabel,
  trailingWindowDays = 90,
  allTimeRecords,
  compareLabel,
  compareDistanceM,
}: {
  activities: ActivitySummary[];
  startDate: string;
  endDate: string;
  periodLabel: string;
  trailingWindowDays?: number;
  /** This athlete's true all-time personal records (personalRecords() over their *entire*
   * running history, not just this period) -- optional so RunningStats still works standalone
   * without it. When provided, the personal-records table below can tell "fastest within this
   * period" (which `records` already is, computed from the period-scoped `activities`) apart
   * from "an actual all-time best that happened to land in this period" (Phase 7's "PBs set"
   * recap ingredient, ADR 0011 decision 3) -- the same date at the same distance in both lists
   * means this period's best *is* the all-time best, not just the best of a narrower slice. */
  allTimeRecords?: PersonalRecord[];
  /** Label for the year-over-year comparison period, e.g. "2025" or "Jul 2025" -- see
   * PeriodStatsCard's own prop of the same name. */
  compareLabel?: string;
  /** This athlete's *running-only* distance for the comparison period -- deliberately a
   * separate number from PeriodStatsCard's all-sport compareDistanceM, since "Kilometers run"
   * here is running-only and comparing it against an all-sport total would be misleading. Shown
   * as "Xkm in <compareLabel>" under the Kilometers run tile, not a +/- delta (same reasoning
   * as PeriodStatsCard's compareMeta). */
  compareDistanceM?: number | null;
}) {
  // Which legend reference distance (if any) is currently hovered, to highlight matching cells.
  const [hoveredLegendKm, setHoveredLegendKm] = useState<number | "none" | null>(null);
  // Driven by onMouseEnter/Leave rather than a CSS :hover rule, matching the legend's own
  // pattern above -- keeps row-highlight behavior consistent and directly testable.
  const [hoveredRecordLabel, setHoveredRecordLabel] = useState<string | null>(null);

  if (activities.length === 0) {
    return (
      <section className="card">
        <h2>Running</h2>
        <p>No running activities recorded in {periodLabel}.</p>
      </section>
    );
  }

  // A month (or shorter) has too few distinct months to bucket a bar chart by, so it buckets by
  // day instead; a year keeps the coarser per-month buckets. Same threshold governs the trailing
  // chart's tick strategy below.
  const spanDays = Math.round(
    (parseIsoDate(endDate).getTime() - parseIsoDate(startDate).getTime()) / MS_PER_DAY,
  ) + 1;
  const useDailyBuckets = spanDays <= 31;
  // A range longer than a year (the all-time view) has too many individual days for a
  // day-per-square heatmap to stay readable -- one square per week instead, one row per year.
  const useYearRows = spanDays > 366;
  // A week-per-cell heatmap needs its own scale (a week and a day aren't the same magnitude of
  // distance) -- see WEEKLY_HEATMAP_SCALE/DAILY_HEATMAP_SCALE in runningStats.ts.
  const heatmapScale = useYearRows ? WEEKLY_HEATMAP_SCALE : DAILY_HEATMAP_SCALE;

  const totalDistanceM = activities.reduce((sum, a) => sum + (a.distance_m ?? 0), 0);
  const totalMovingDurationS = activities.reduce((sum, a) => sum + (effectiveDurationS(a) ?? 0), 0);
  const longest = activities.reduce((max, a) =>
    (a.distance_m ?? 0) > (max.distance_m ?? 0) ? a : max,
  );
  const activeDates = distinctActiveDates(activities);
  const streaks = longestStreakAndBreak(activeDates);
  const weekdays = weekdayStats(activities);
  const mostOften = weekdays.reduce((max, d) => (d.count > max.count ? d : max));
  const leastOften = weekdays.reduce((min, d) => (d.count < min.count ? d : min));
  const { am, pm } = amPmCounts(activities);
  const records = personalRecords(activities);
  const newPrsThisPeriod = newAllTimePrs(records, allTimeRecords ?? []);
  const newPrLabels = new Set(newPrsThisPeriod.map((r) => r.label));
  const compareMeta =
    compareLabel != null && compareDistanceM != null
      ? `${(compareDistanceM / 1000).toFixed(0)}km in ${compareLabel}`
      : null;

  const bucketData: DistanceBucket[] = useDailyBuckets
    ? distanceByDay(activities, startDate, endDate)
    : useYearRows
      ? distanceByYear(activities, startDate, endDate)
      : monthlyDistanceM(activities).map((m, i) => ({
          label: monthName(i + 1).slice(0, 3),
          km: Math.round(m / 100) / 10,
        }));
  const bucketTickInterval = useDailyBuckets ? Math.max(0, Math.ceil(bucketData.length / 8) - 1) : 0;
  const rollingData = rollingDistanceKm(activities, startDate, endDate, trailingWindowDays).map(
    (p) => ({
      local_date: p.local_date,
      km: Math.round(p.distanceM / 100) / 10,
    }),
  );
  const trailingTicks = useDailyBuckets
    ? rollingData.map((p) => p.local_date).filter((_, i) => i % 5 === 0)
    : useYearRows
      ? rollingData.map((p) => p.local_date).filter((d) => d.endsWith("-01-01"))
      : rollingData.map((p) => p.local_date).filter((d) => d.endsWith("-01"));
  const trailingTickFormatter = useDailyBuckets
    ? (iso: string) => iso.slice(8, 10)
    : useYearRows
      ? (iso: string) => iso.slice(0, 4)
      : (iso: string) => monthName(Number(iso.slice(5, 7))).slice(0, 3);
  const scatterData = scatterPointOpacities(
    activities
      .filter((a) => a.distance_m && effectiveDurationS(a))
      .map((a) => ({
        km: Math.round((a.distance_m! / 1000) * 10) / 10,
        pace: effectiveDurationS(a)! / 60 / (a.distance_m! / 1000),
      })),
  );

  const dailyByDate = dailyStats(activities);

  function heatmapDayFor(iso: string): HeatmapDay {
    const day = dailyByDate.get(iso);
    return {
      date: iso,
      km: (day?.distanceM ?? 0) / 1000,
      paceMinPerKm: day && day.distanceM > 0 ? day.durationS / 60 / (day.distanceM / 1000) : null,
      elevationM: day?.elevationGainM ?? 0,
    };
  }

  // A month (or shorter) is small enough to lay every day out on one line -- the week-grid
  // below exists specifically to keep a year's 365 days legible, which isn't a concern here.
  const days: HeatmapDay[] = [];
  if (useDailyBuckets) {
    const cursor = parseIsoDate(startDate);
    const end = parseIsoDate(endDate);
    while (cursor <= end) {
      days.push(heatmapDayFor(cursor.toISOString().slice(0, 10)));
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
  }

  const weeks: HeatmapDay[][] = [];
  if (!useDailyBuckets && !useYearRows) {
    const rangeStart = parseIsoDate(startDate);
    const startWeekday = (rangeStart.getUTCDay() + 6) % 7;
    const cursor = new Date(rangeStart);
    cursor.setUTCDate(cursor.getUTCDate() - startWeekday);
    const end = parseIsoDate(endDate);
    let week: HeatmapDay[] = [];
    while (cursor <= end || week.length > 0) {
      week.push(heatmapDayFor(cursor.toISOString().slice(0, 10)));
      if (week.length === 7) {
        weeks.push(week);
        week = [];
      }
      cursor.setUTCDate(cursor.getUTCDate() + 1);
      if (cursor > end && week.length === 0) break;
    }
    if (week.length > 0) weeks.push(week);
  }

  // One row per calendar year, one square per Monday-starting week of that year -- every row
  // uses the same week-of-year column positions (each year's own Jan-1-aligned Monday grid),
  // so distance across years lines up visually even though ISO week boundaries can drift
  // slightly year to year.
  const yearRows: { year: number; weeks: HeatmapWeek[] }[] = [];
  if (useYearRows) {
    const weeklyTotals = new Map<string, { distanceM: number; durationS: number; elevationM: number }>();
    for (const a of activities) {
      if (!a.local_date) continue;
      const monday = isoDate(mondayOf(parseIsoDate(a.local_date)));
      const existing = weeklyTotals.get(monday) ?? { distanceM: 0, durationS: 0, elevationM: 0 };
      existing.distanceM += a.distance_m ?? 0;
      existing.durationS += effectiveDurationS(a) ?? 0;
      existing.elevationM += a.elevation_gain_m ?? 0;
      weeklyTotals.set(monday, existing);
    }
    const startYear = Number(startDate.slice(0, 4));
    const endYear = Number(endDate.slice(0, 4));
    for (let year = startYear; year <= endYear; year++) {
      const yearWeeks: HeatmapWeek[] = [];
      const cursor = mondayOf(parseIsoDate(`${year}-01-01`));
      const yearEnd = parseIsoDate(`${year}-12-31`);
      while (cursor <= yearEnd) {
        const iso = isoDate(cursor);
        const wk = weeklyTotals.get(iso);
        const km = (wk?.distanceM ?? 0) / 1000;
        yearWeeks.push({
          weekStart: iso,
          km,
          paceMinPerKm: wk && wk.distanceM > 0 ? wk.durationS / 60 / (wk.distanceM / 1000) : null,
          elevationM: wk?.elevationM ?? 0,
        });
        cursor.setUTCDate(cursor.getUTCDate() + 7);
      }
      yearRows.push({ year, weeks: yearWeeks });
    }
  }

  const weeksElapsed = Math.max(1, activeDates.length > 0 ? daysSpan(activeDates) / 7 : 1);

  function daysSpan(dates: string[]): number {
    if (dates.length === 0) return 0;
    const first = new Date(dates[0]!).getTime();
    const lastDate = new Date(dates[dates.length - 1]!).getTime();
    return Math.max(1, (lastDate - first) / 86_400_000 + 1);
  }

  // Shared by both the daily grid/strip and the year-rows weekly grid. The tiered gradient/pie
  // encoding is the same in both; only the thresholds differ, since a cell means one day in the
  // year/month views and one whole week in the all-time view (see `heatmapScale` above).
  function renderHeatmapCellCommon(
    key: string,
    km: number,
    paceMinPerKm: number | null,
    elevationM: number,
    tooltipTitle: string,
    extraClassName = "",
  ) {
    const long = isLongRun(km, heatmapScale);
    const bucket = km > 0 ? nearestLegendKm(km, heatmapScale) : "none";
    const hoverClass =
      hoveredLegendKm == null ? "" : bucket === hoveredLegendKm ? " is-highlighted" : " is-dimmed";
    return (
      <span
        key={key}
        className={`running-heatmap__cell${extraClassName}${hoverClass}`}
        style={
          { "--heat-pct": `${long ? 0 : shortRunHeatPct(km, heatmapScale)}%` } as React.CSSProperties
        }
      >
        {long && (
          <span
            className="running-heatmap__pie"
            style={{ "--pie-deg": `${longRunPieDeg(km, heatmapScale)}deg` } as React.CSSProperties}
          />
        )}
        <span className="running-heatmap__tooltip">
          <strong>{tooltipTitle}</strong>
          {km > 0 ? (
            <span>
              {km.toFixed(1)} km · {paceMinPerKm!.toFixed(2)} min/km
              {elevationM > 0 && ` · +${Math.round(elevationM)}m`}
            </span>
          ) : (
            <span>No run</span>
          )}
        </span>
      </span>
    );
  }

  function renderHeatmapCell(cell: HeatmapDay, extraClassName = "") {
    return renderHeatmapCellCommon(
      cell.date,
      cell.km,
      cell.paceMinPerKm,
      cell.elevationM,
      formatHeatmapDate(cell.date),
      extraClassName,
    );
  }

  function renderHeatmapWeekCell(week: HeatmapWeek) {
    return renderHeatmapCellCommon(
      week.weekStart,
      week.km,
      week.paceMinPerKm,
      week.elevationM,
      formatWeekLabel(week.weekStart),
    );
  }

  const amPmData = [
    { name: "AM", value: am },
    { name: "PM", value: pm },
  ];
  const totalRuns = am + pm;
  const pmShare = totalRuns > 0 ? Math.round((pm / totalRuns) * 100) : 0;

  return (
    <section className="card running-stats">
      <h2>Running</h2>
      <div className="stat-grid">
        <StatTile
          label="Kilometers run"
          value={(totalDistanceM / 1000).toFixed(0)}
          unit="km"
          meta={compareMeta}
          icon="route"
          tone="pace"
          hero
        />
        <StatTile
          label="Average pace"
          value={formatPaceMinPerKm(totalMovingDurationS, totalDistanceM)}
          unit="/km"
          icon="clock"
          tone="pace"
          hero
        />
        <StatTile
          label="Longest run"
          value={((longest.distance_m ?? 0) / 1000).toFixed(1)}
          unit="km"
          icon="trophy"
          tone="load"
          hero
        />
        <StatTile
          label="Average run length"
          value={(totalDistanceM / 1000 / activities.length).toFixed(1)}
          unit="km"
          icon="route"
          tone="pace"
        />
        <StatTile
          label="Average days run/week"
          value={(activeDates.length / weeksElapsed).toFixed(1)}
          icon="calendar"
          tone="elevation"
        />
        <StatTile
          label="Most often run on"
          value={weekdayLabel(mostOften.day)}
          icon="calendar"
          tone="elevation"
        />
        <StatTile
          label="Least often run on"
          value={weekdayLabel(leastOften.day)}
          icon="calendar"
          tone="neutral"
        />
        <StatTile
          label="Longest streak"
          value={streaks.longestStreakDays}
          unit="days"
          meta={formatDateRange(streaks.longestStreakStart, streaks.longestStreakEnd)}
          icon="flame"
          tone="load"
        />
        <StatTile
          label="Longest break"
          value={streaks.longestBreakDays}
          unit="days"
          meta={formatDateRange(streaks.longestBreakStart, streaks.longestBreakEnd)}
          icon="moon"
          tone="neutral"
        />
      </div>

      <div className="running-stats__charts">
        <div>
          <h3>
            {useDailyBuckets ? "Distance per day" : useYearRows ? "Distance per year" : "Distance per month"}
          </h3>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={bucketData}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="label"
                interval={bucketTickInterval}
                stroke="var(--color-text-muted)"
                fontSize={11}
              />
              <YAxis stroke="var(--color-text-muted)" fontSize={11} width={32} />
              <Tooltip
                contentStyle={{
                  background: "var(--color-surface-raised)",
                  border: "1px solid var(--color-border)",
                }}
              />
              <Bar dataKey="km" fill="var(--color-pace)" isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div>
          <h3>Trailing {trailingWindowDays}-day kilometers</h3>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={rollingData}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
              <XAxis
                dataKey="local_date"
                stroke="var(--color-text-muted)"
                fontSize={11}
                ticks={trailingTicks}
                tickFormatter={trailingTickFormatter}
              />
              <YAxis stroke="var(--color-text-muted)" fontSize={11} width={32} />
              <Tooltip
                contentStyle={{
                  background: "var(--color-surface-raised)",
                  border: "1px solid var(--color-border)",
                }}
              />
              <Line
                type="monotone"
                dataKey="km"
                stroke="var(--color-pace)"
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div>
          <h3>Pace vs distance</h3>
          <ResponsiveContainer width="100%" height={180}>
            <ScatterChart>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
              <XAxis
                dataKey="km"
                type="number"
                name="Distance"
                stroke="var(--color-text-muted)"
                fontSize={11}
              />
              <YAxis
                dataKey="pace"
                type="number"
                name="Pace"
                stroke="var(--color-text-muted)"
                fontSize={11}
                width={40}
                reversed
                domain={["dataMin - 0.3", "dataMax + 0.3"]}
                tickFormatter={(v: number) => formatMinPerKm(v)}
              />
              <Tooltip
                cursor={{ strokeDasharray: "3 3" }}
                formatter={(value, name) =>
                  name === "Pace" ? [`${formatMinPerKm(Number(value))} /km`, name] : [`${value} km`, name]
                }
                contentStyle={{
                  background: "var(--color-surface-raised)",
                  border: "1px solid var(--color-border)",
                }}
              />
              <Scatter data={scatterData} isAnimationActive={false}>
                {scatterData.map((entry, i) => (
                  <Cell key={i} fill="var(--color-pace)" fillOpacity={entry.opacity} />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>

        <div>
          <h3>Time of day</h3>
          <ResponsiveContainer width="100%" height={180}>
            <PieChart>
              <Pie
                data={amPmData}
                dataKey="value"
                nameKey="name"
                innerRadius={45}
                outerRadius={70}
                isAnimationActive={false}
              >
                <Cell fill="var(--color-pace)" />
                <Cell fill="var(--color-elevation)" />
              </Pie>
              <Tooltip
                contentStyle={{
                  background: "var(--color-surface-raised)",
                  border: "1px solid var(--color-border)",
                }}
              />
            </PieChart>
          </ResponsiveContainer>
          <ChartLegend
            center
            items={[
              { label: `AM ${am}`, color: "var(--color-pace)" },
              { label: `PM ${pm}`, color: "var(--color-elevation)" },
            ]}
          />
          <p className="running-stats__pie-caption">
            {pmShare}% of runs are in the afternoon/evening (local time)
          </p>
        </div>
      </div>

      <div className="running-heatmap">
        <h3>{useYearRows ? "Weekly distance" : "Daily distance"}</h3>
        {useDailyBuckets ? (
          <div
            className="running-heatmap__strip"
            style={{ "--day-count": days.length } as React.CSSProperties}
          >
            {days.map((day) => (
              <div className="running-heatmap__strip-day" key={day.date}>
                {renderHeatmapCell(day)}
                <span className="running-heatmap__day-label">{parseIsoDate(day.date).getUTCDate()}</span>
              </div>
            ))}
          </div>
        ) : useYearRows ? (
          <div
            className="running-heatmap__grid running-heatmap__grid--years"
            style={
              {
                "--week-count": Math.max(0, ...yearRows.map((r) => r.weeks.length)),
              } as React.CSSProperties
            }
          >
            {yearRows.map((row) => (
              <div className="running-heatmap__row" key={row.year}>
                <span className="running-heatmap__row-label">{row.year}</span>
                {row.weeks.map((week) => renderHeatmapWeekCell(week))}
              </div>
            ))}
          </div>
        ) : (
          <div
            className="running-heatmap__grid"
            style={{ "--week-count": weeks.length } as React.CSSProperties}
          >
            <div className="running-heatmap__row running-heatmap__row--months">
              <span className="running-heatmap__row-label" />
              {weeks.map((week, i) => {
                const label = monthLabelFor(week, weeks[i - 1], startDate, endDate);
                return (
                  <span key={week[0]!.date} className="running-heatmap__month-label">
                    {label}
                  </span>
                );
              })}
            </div>
            {WEEKDAY_ROWS.map((label, row) => (
              <div className="running-heatmap__row" key={label}>
                <span className="running-heatmap__row-label">{label}</span>
                {weeks.map((week, i) => {
                  const monthEnd = isMonthEndWeek(week, weeks[i + 1], startDate, endDate);
                  const cell = week[row];
                  if (!cell || !inRange(cell.date, startDate, endDate)) {
                    return (
                      <span
                        key={week[0]!.date}
                        className={`running-heatmap__cell is-empty${monthEnd ? " is-month-end" : ""}`}
                      />
                    );
                  }
                  return renderHeatmapCell(cell, monthEnd ? " is-month-end" : "");
                })}
              </div>
            ))}
          </div>
        )}
        <div className="running-heatmap__legend" onMouseLeave={() => setHoveredLegendKm(null)}>
          <span
            className="running-heatmap__legend-item"
            onMouseEnter={() => setHoveredLegendKm("none")}
          >
            <span className="running-heatmap__cell" style={{ "--heat-pct": "0%" } as React.CSSProperties} />
            <span>No run</span>
          </span>
          {heatmapScale.gradientLegendKm.map((km) => (
            <span
              key={km}
              className="running-heatmap__legend-item"
              onMouseEnter={() => setHoveredLegendKm(km)}
            >
              <span
                className="running-heatmap__cell"
                style={{ "--heat-pct": `${shortRunHeatPct(km, heatmapScale)}%` } as React.CSSProperties}
              />
              <span>{km}km</span>
            </span>
          ))}
          {heatmapScale.pieLegendKm.map((km) => (
            <span
              key={km}
              className="running-heatmap__legend-item"
              onMouseEnter={() => setHoveredLegendKm(km)}
            >
              <span className="running-heatmap__cell">
                <span
                  className="running-heatmap__pie"
                  style={{ "--pie-deg": `${longRunPieDeg(km, heatmapScale)}deg` } as React.CSSProperties}
                />
              </span>
              <span>{km}km</span>
            </span>
          ))}
        </div>
      </div>

      {records.length > 0 && (
        <div className="running-records">
          <h3>
            Personal records — {periodLabel}
            <span className="running-records__caveat">
              {" "}
              (fastest whole recorded run near each distance, not a true best-effort segment)
            </span>
          </h3>
          {newPrsThisPeriod.length > 0 && (
            <p className="running-records__new-prs">
              <Icon name="trophy" /> {newPrsThisPeriod.length} all-time PR
              {newPrsThisPeriod.length === 1 ? "" : "s"} set this period:{" "}
              {newPrsThisPeriod.map((r) => r.label).join(", ")}
            </p>
          )}
          <table className="running-records__table">
            <thead>
              <tr>
                <th>Distance</th>
                <th>Date</th>
                <th>Pace</th>
                <th>Speed</th>
                <th>Distance run</th>
                <th>Time</th>
                <th>Runs</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => {
                const isAllTimePr = newPrLabels.has(r.label);
                return (
                  <tr
                    key={r.label}
                    className={hoveredRecordLabel === r.label ? "is-hovered" : ""}
                    onMouseEnter={() => setHoveredRecordLabel(r.label)}
                    onMouseLeave={() => setHoveredRecordLabel(null)}
                  >
                    <td>{r.label}</td>
                    <td>
                      {r.date}
                      {isAllTimePr && (
                        <span className="running-records__pr-badge" title="All-time PR">
                          <Icon name="trophy" />
                        </span>
                      )}
                    </td>
                    <td>{formatMinPerKm(r.paceMinPerKm)} /km</td>
                    <td>{r.speedKmh.toFixed(2)} km/h</td>
                    <td>{(r.actualDistanceM / 1000).toFixed(2)} km</td>
                    <td>{formatDuration(r.durationS)}</td>
                    <td>{r.eligibleCount}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const WEEKDAY_ROWS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function monthLabelFor(
  week: HeatmapDay[],
  previousWeek: HeatmapDay[] | undefined,
  startDate: string,
  endDate: string,
): string {
  const firstInRange = week.find((d) => inRange(d.date, startDate, endDate));
  if (!firstInRange) return "";
  const month = firstInRange.date.slice(5, 7);
  const prevFirstInRange = previousWeek?.find((d) => inRange(d.date, startDate, endDate));
  const prevMonth = prevFirstInRange?.date.slice(5, 7);
  if (month === prevMonth) return "";
  return monthName(Number(month)).slice(0, 3);
}
