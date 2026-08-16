// The "<period> stats" summary card shared by YearView and MonthView -- computed entirely
// from a flat activity list (no rollup dependency), so the same card works at any date-range
// grain. The "busiest <bucket>" tile is the one thing that's genuinely period-shaped (a month
// within a year vs. a week within a month), so the caller supplies its label/value directly
// rather than this component knowing about years or months at all.
import { useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import type { ActivitySummary } from "../api/types";
import { sportStyle, toneColor } from "../metricStyle";
import {
  distinctActiveDates,
  effectiveDurationS,
  formatDurationHM,
  longestStreakAndBreak,
  weekdayLabel,
  weekdayStats,
} from "../runningStats";
import { StatTile } from "./StatTile";
import {
  activityTypeCounts,
  averageDistanceM,
  averageDurationS,
  averageHeartRateBpm,
  averageSpeedKmh,
  longestActivity,
  maxHeartRateBpm,
  totalElevationM,
} from "../yearStats";

function sumOrNull(values: (number | null)[]): number | null {
  const present = values.filter((v): v is number => v != null);
  return present.length > 0 ? present.reduce((a, b) => a + b, 0) : null;
}

export function PeriodStatsCard({
  title,
  activities,
  busiestLabel,
  busiestValue,
  compareLabel,
  compareDistanceM,
}: {
  title: string;
  activities: ActivitySummary[];
  busiestLabel: string;
  busiestValue: string | null;
  /** Label for the year-over-year comparison period, e.g. "2025" or "Aug 2025" -- the caller
   * names what it's comparing against since only it knows whether that's a prior year, a prior
   * month, or something else (ADR 0011 decision 3, "year-over-year deltas"). */
  compareLabel?: string;
  /** Total distance for the comparison period, from a second, separately-fetched activity
   * list, shown as a small "Xkm in <compareLabel>" caption under the Distance tile (the user
   * explicitly wants the comparison period's own total here, not a +/- delta -- a delta read as
   * disconnected from the number it was relative to). Null (not just absent) when there's
   * genuinely no prior-period data to compare against (e.g. the athlete's very first year) --
   * distinct from "not provided at all" so callers that don't support this comparison yet can
   * simply omit both props. */
  compareDistanceM?: number | null;
}) {
  // Declared before the early return below -- React's rules of hooks require every hook to run
  // on every render, and this component bails out early when there's nothing to show.
  const [typeMetric, setTypeMetric] = useState<"count" | "time">("count");

  if (activities.length === 0) return null;

  const distanceM = sumOrNull(activities.map((a) => a.distance_m));
  const movingDurationS = sumOrNull(activities.map((a) => effectiveDurationS(a)));
  const activeDates = distinctActiveDates(activities);
  const streaks = longestStreakAndBreak(activeDates);
  const weekdays = weekdayStats(activities);
  const favoriteDay = weekdays.reduce((max, d) => (d.count > max.count ? d : max));
  const longest = longestActivity(activities);
  const avgDistanceM = averageDistanceM(activities);
  const avgDurationS = averageDurationS(activities);
  const avgSpeedKmh = averageSpeedKmh(activities);
  const avgHr = averageHeartRateBpm(activities);
  const maxHr = maxHeartRateBpm(activities);
  const typeCounts = activityTypeCounts(activities);
  const compareMeta =
    compareLabel != null && compareDistanceM != null
      ? `${(compareDistanceM / 1000).toFixed(0)}km in ${compareLabel}`
      : null;

  return (
    <section className="card">
      <h2>{title}</h2>
      {/* Three heroes, no more: total distance, the streak, and total climbing -- the figures
          worth reading from across the room. Everything else stays a neutral tile with a
          coloured icon, which is what keeps the three tinted ones reading as emphasis. */}
      <div className="stat-grid">
        {distanceM != null && (
          <StatTile
            label="Distance"
            value={(distanceM / 1000).toFixed(0)}
            unit="km"
            meta={compareMeta}
            icon="route"
            tone="pace"
            hero
          />
        )}
        <StatTile
          label="Longest streak"
          value={streaks.longestStreakDays}
          unit="days"
          icon="flame"
          tone="load"
          hero
        />
        <StatTile
          label="Total elevation"
          value={totalElevationM(activities).toFixed(0)}
          unit="m"
          icon="mountain"
          tone="elevation"
          hero
        />
        <StatTile label="Activities" value={activities.length} icon="calendar" tone="pace" />
        {movingDurationS != null && (
          <StatTile
            label="Moving time"
            value={(movingDurationS / 3600).toFixed(0)}
            unit="h"
            icon="clock"
            tone="cadence"
          />
        )}
        <StatTile
          label="Active days"
          value={activeDates.length}
          icon="calendar"
          tone="elevation"
        />
        {longest && (
          <StatTile
            label="Longest activity"
            value={(longest.distanceM / 1000).toFixed(1)}
            unit="km"
            meta={`on ${longest.date}`}
            icon="trophy"
            tone="load"
          />
        )}
        {busiestValue != null && (
          <StatTile label={busiestLabel} value={busiestValue} icon="calendar" tone="pace" />
        )}
        <StatTile
          label="Favorite day"
          value={weekdayLabel(favoriteDay.day)}
          icon="calendar"
          tone="pace"
        />
        {avgDistanceM != null && (
          <StatTile
            label="Average distance"
            value={(avgDistanceM / 1000).toFixed(1)}
            unit="km"
            meta="per activity with a distance"
            icon="route"
            tone="pace"
          />
        )}
        {avgDurationS != null && (
          <StatTile
            label="Average time"
            value={formatDurationHM(avgDurationS)}
            meta="per activity"
            icon="clock"
            tone="cadence"
          />
        )}
        {avgSpeedKmh != null && (
          <StatTile
            label="Average speed"
            value={avgSpeedKmh.toFixed(1)}
            unit="km/h"
            meta="per activity with a speed"
            icon="gauge"
            tone="pace"
          />
        )}
        {avgHr != null && (
          <StatTile
            label="Average heart rate"
            value={avgHr.toFixed(0)}
            unit="bpm"
            icon="heart"
            tone="hr"
          />
        )}
        {maxHr != null && (
          <StatTile
            label="Max heart rate"
            value={maxHr.toFixed(0)}
            unit="bpm"
            icon="heart"
            tone="hr"
          />
        )}
      </div>

      {typeCounts.length > 0 && (
        <>
          <div className="type-breakdown__header">
            <h3>Activities by type</h3>
            {/* Count and time can rank sports differently -- a single 4-hour ride outweighs
                a dozen 15-minute yoga sessions on the clock but not on the tally, and the
                pie's own proportions are the whole point of showing this at all. */}
            <div className="type-breakdown__toggle" role="group" aria-label="Show by">
              <button
                type="button"
                className={typeMetric === "count" ? "is-active" : undefined}
                onClick={() => setTypeMetric("count")}
              >
                # activities
              </button>
              <button
                type="button"
                className={typeMetric === "time" ? "is-active" : undefined}
                onClick={() => setTypeMetric("time")}
              >
                Time
              </button>
            </div>
          </div>
          {/* No legend -- with up to a dozen-plus sports, a legend list ends up several times
              the pie's own size for very little reading value; hovering a slice already answers
              "which sport, how much" directly on the shape it's asking about. */}
          <div className="type-breakdown">
            <ResponsiveContainer width={220} height={220}>
              <PieChart>
                <Pie
                  data={typeCounts}
                  dataKey={typeMetric === "count" ? "count" : "durationS"}
                  nameKey="sport"
                  innerRadius={56}
                  outerRadius={104}
                  paddingAngle={typeCounts.length > 1 ? 2 : 0}
                  isAnimationActive={false}
                >
                  {typeCounts.map((t) => (
                    <Cell key={t.sport} fill={toneColor(sportStyle(t.sport).tone)} />
                  ))}
                </Pie>
                <Tooltip
                  // Pie tooltips have no natural axis label (unlike Cartesian charts), so
                  // `labelFormatter` never fires here -- the sport name has to come through
                  // `formatter`'s own second argument (from `nameKey="sport"` above) instead,
                  // reformatted into the same display form used everywhere else a sport name
                  // is shown ("rock_climbing" -> "rock climbing").
                  formatter={(value, name) => [
                    typeMetric === "count" ? String(value) : formatDurationHM(Number(value)),
                    String(name).replace(/_/g, " "),
                  ]}
                  contentStyle={{
                    background: "var(--color-surface-raised)",
                    border: "1px solid var(--color-border)",
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </section>
  );
}
