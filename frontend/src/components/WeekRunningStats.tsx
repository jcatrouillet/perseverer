// The "Running" card on the week view: how this week's running compares to recent history,
// mirroring the kind of week-in-review page intervals.icu shows (reference screenshots this
// session) but built entirely from this athlete's own real archive -- no reproduction of any
// vendor's internal scoring.
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import type { ActivitySummary } from "../api/types";
import { Icon } from "./Icon";
import {
  effectiveDurationS,
  formatDurationHM,
  formatMinPerKm,
  isPlausibleRunPace,
  metMinutes,
  newAllTimePrs,
  personalRecords,
  type PersonalRecord,
  weekdayIndex,
  weekdayLabel,
  weeklyDistanceSeries,
} from "../runningStats";
import { MetricChip, StatTile } from "./StatTile";
import "../styles/running-stats.css";

function formatWeekTick(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function WeekRunningStats({
  runningActivities,
  rangeStart,
  rangeEnd,
  weekStart,
  weekEnd,
  priorWeekStart,
  priorWeekEnd,
  allTimeRecords,
}: {
  /** Running activities across `rangeStart`..`rangeEnd` (a trailing window, e.g. the last 12
   * months) -- includes this week's own runs, used both for the weekly-distance bar chart and
   * as the pace-vs-distance backdrop this week's runs are highlighted against. */
  runningActivities: ActivitySummary[];
  rangeStart: string;
  rangeEnd: string;
  weekStart: string;
  weekEnd: string;
  /** The immediately preceding calendar week's bounds, used to show *that* week's own running
   * distance as a small caption on the Total distance tile (a real prior total, not a +/- delta
   * the user found disconnected from the number it was relative to -- see PeriodStatsCard's
   * compareMeta and WeekView's priorWeekMeta). Computed here (not passed as a single number) so
   * it goes through the same plausible-pace filtering as every other total on this card. */
  priorWeekStart: string;
  priorWeekEnd: string;
  /** This athlete's true all-time personal records -- see RunningStats.tsx's own prop
   * docstring. Optional so the component still works without it. */
  allTimeRecords?: PersonalRecord[];
}) {
  // A handful of real activities in this athlete's archive are tagged sport=running by the
  // device/export but are actually hikes (real GPS tracks, ~3.4 km/h average speed, mountain
  // altitude -- confirmed, not corrupted data) -- see isPlausibleRunPace's own comment. Excluded
  // here, before any aggregate is computed, so they can't skew this week's own totals, the
  // weekly-distance history, or the pace-vs-distance backdrop.
  const plausibleRuns = runningActivities.filter((a) => {
    const durationS = effectiveDurationS(a);
    if (a.distance_m == null || a.distance_m <= 0 || durationS == null) return true;
    return isPlausibleRunPace(durationS, a.distance_m);
  });

  const thisWeek = plausibleRuns.filter(
    (a) => a.local_date != null && a.local_date >= weekStart && a.local_date <= weekEnd,
  );
  if (thisWeek.length === 0) return null;

  // Phase 7 "PBs set" recap ingredient (ADR 0011 decision 3) -- same cross-reference as
  // RunningStats.tsx's year/month tables, just without the full records table this card
  // doesn't otherwise have.
  const newPrsThisWeek = newAllTimePrs(personalRecords(thisWeek), allTimeRecords ?? []);

  const totalDistanceM = thisWeek.reduce((sum, a) => sum + (a.distance_m ?? 0), 0);
  const totalDurationS = thisWeek.reduce((sum, a) => sum + (effectiveDurationS(a) ?? 0), 0);
  const totalCalories = thisWeek.reduce((sum, a) => sum + (a.calories ?? 0), 0);
  const totalElevationM = thisWeek.reduce((sum, a) => sum + (a.elevation_gain_m ?? 0), 0);
  const fastestPaceMinPerKm = thisWeek
    .filter((a) => a.distance_m != null && a.distance_m > 0 && effectiveDurationS(a) != null)
    .reduce<number | null>((fastest, a) => {
      const pace = effectiveDurationS(a)! / 60 / (a.distance_m! / 1000);
      return fastest == null || pace < fastest ? pace : fastest;
    }, null);
  const dayEquivalentPct = (totalDurationS / 86400) * 100;
  const priorWeekRuns = plausibleRuns.filter(
    (a) => a.local_date != null && a.local_date >= priorWeekStart && a.local_date <= priorWeekEnd,
  );
  const priorWeekDistanceM = priorWeekRuns.reduce((sum, a) => sum + (a.distance_m ?? 0), 0);
  const priorWeekMeta = `${(priorWeekDistanceM / 1000).toFixed(1)}km previous week`;

  // METs (see metMinutes' own docstring for the standard gross-MET formula) -- grouped by day,
  // skipping any run missing calories or a body-weight reading rather than guessing either.
  const metsByDate = new Map<string, number>();
  for (const a of thisWeek) {
    if (a.local_date == null || a.calories == null || a.weight_kg == null) continue;
    metsByDate.set(
      a.local_date,
      (metsByDate.get(a.local_date) ?? 0) + metMinutes(a.calories, a.weight_kg),
    );
  }
  const metsDays = [...metsByDate.entries()].sort(([a], [b]) => (a < b ? -1 : 1));
  const totalMets = metsDays.reduce((sum, [, mets]) => sum + mets, 0);

  const weeklySeries = weeklyDistanceSeries(plausibleRuns, rangeStart, rangeEnd);

  const scatterPoints = plausibleRuns
    .filter((a) => a.distance_m != null && a.distance_m > 0 && effectiveDurationS(a) != null)
    .map((a) => ({
      id: a.id,
      km: Math.round((a.distance_m! / 1000) * 10) / 10,
      pace: effectiveDurationS(a)! / 60 / (a.distance_m! / 1000),
      isThisWeek: a.local_date != null && a.local_date >= weekStart && a.local_date <= weekEnd,
    }))
    // SVG has no z-index -- later points in the array paint over earlier ones. `runningActivities`
    // arrives newest-first, so without this, this week's own runs (the most recent ones) would be
    // painted *first* and then covered by the faded older background points drawn after them.
    // Sorting this week's points to the end guarantees they're always painted last, on top.
    .sort((a, b) => Number(a.isThisWeek) - Number(b.isThisWeek));

  return (
    <section className="card running-stats">
      <h2>Running</h2>
      {newPrsThisWeek.length > 0 && (
        <p className="running-records__new-prs">
          <Icon name="trophy" /> {newPrsThisWeek.length} all-time PR
          {newPrsThisWeek.length === 1 ? "" : "s"} set this week:{" "}
          {newPrsThisWeek.map((r) => r.label).join(", ")}
        </p>
      )}
      <div className="stat-grid">
        <StatTile
          label="Total distance"
          value={(totalDistanceM / 1000).toFixed(1)}
          unit="km"
          meta={priorWeekMeta}
          icon="route"
          tone="pace"
          hero
        />
        <StatTile
          label="Total duration"
          value={formatDurationHM(totalDurationS)}
          icon="clock"
          tone="cadence"
          hero
        />
        {totalMets > 0 && (
          <StatTile
            label="Total METs"
            value={Math.round(totalMets)}
            icon="flame"
            tone="load"
            hero
          />
        )}
        {fastestPaceMinPerKm != null && (
          <StatTile
            label="Fastest pace"
            value={formatMinPerKm(fastestPaceMinPerKm)}
            unit="/km"
            icon="gauge"
            tone="pace"
          />
        )}
        <StatTile
          label="Day equivalent"
          value={dayEquivalentPct.toFixed(0)}
          unit="%"
          meta="of a full day spent running"
          icon="calendar"
          tone="elevation"
        />
        {totalCalories > 0 && (
          <StatTile
            label="Calories burned"
            value={Math.round(totalCalories)}
            unit="kcal"
            icon="flame"
            tone="load"
          />
        )}
        {totalElevationM > 0 && (
          <StatTile
            label="Total elevation"
            value={totalElevationM.toFixed(0)}
            unit="m"
            icon="mountain"
            tone="elevation"
          />
        )}
      </div>

      {metsDays.length > 0 && (
        <div className="week-mets-breakdown">
          {metsDays.map(([date, mets]) => (
            <MetricChip
              key={date}
              label={`${weekdayLabel(weekdayIndex(date))} ${Math.round(mets)} METs`}
              icon="flame"
              tone="load"
            />
          ))}
        </div>
      )}

      <div className="running-stats__charts">
        <div>
          <h3>Weekly distance</h3>
          <ResponsiveContainer width="100%" height={140}>
            <BarChart data={weeklySeries}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="weekStart"
                stroke="var(--color-text-muted)"
                fontSize={11}
                tickFormatter={formatWeekTick}
                interval={Math.max(0, Math.ceil(weeklySeries.length / 8) - 1)}
              />
              <YAxis stroke="var(--color-text-muted)" fontSize={11} width={32} />
              <Tooltip
                labelFormatter={(v) => `Week of ${formatWeekTick(String(v))}`}
                formatter={(value) => [`${value} km`, "Distance"]}
                contentStyle={{
                  background: "var(--color-surface-raised)",
                  border: "1px solid var(--color-border)",
                }}
              />
              <Bar dataKey="km" isAnimationActive={false}>
                {weeklySeries.map((p) => (
                  <Cell
                    key={p.weekStart}
                    fill={p.weekStart === weekStart ? "var(--color-pace)" : "var(--color-text-faint)"}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div>
          <h3>Pace vs distance</h3>
          <ResponsiveContainer width="100%" height={140}>
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
              <ZAxis range={[36, 90]} />
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
              <Scatter data={scatterPoints} isAnimationActive={false}>
                {scatterPoints.map((p) => (
                  <Cell
                    key={p.id}
                    fill={p.isThisWeek ? "var(--color-pace)" : "var(--color-text-faint)"}
                    fillOpacity={p.isThisWeek ? 1 : 0.35}
                  />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  );
}
