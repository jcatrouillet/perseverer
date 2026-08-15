// The "<period> stats" summary card shared by YearView and MonthView -- computed entirely
// from a flat activity list (no rollup dependency), so the same card works at any date-range
// grain. The "busiest <bucket>" tile is the one thing that's genuinely period-shaped (a month
// within a year vs. a week within a month), so the caller supplies its label/value directly
// rather than this component knowing about years or months at all.
import type { ActivitySummary } from "../api/types";
import { sportStyle } from "../metricStyle";
import {
  distinctActiveDates,
  effectiveDurationS,
  formatDurationHM,
  longestStreakAndBreak,
  weekdayLabel,
  weekdayStats,
} from "../runningStats";
import { MetricChip, StatTile } from "./StatTile";
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
          <h3>Activities by type</h3>
          {/* A ranked list rather than a tile grid: the counts span two orders of magnitude
              (630 runs vs. 2 racket sessions), and a proportional bar shows that where
              fourteen equal-sized tiles flatten it. */}
          <ul className="type-list">
            {typeCounts.map((t) => {
              const style = sportStyle(t.sport);
              return (
                <li key={t.sport} className={`type-list__row tone-${style.tone}`}>
                  <MetricChip
                    label={t.sport.replace(/_/g, " ")}
                    icon={style.icon}
                    tone={style.tone}
                  />
                  <span className="type-list__bar">
                    <i style={{ width: `${(t.count / typeCounts[0]!.count) * 100}%` }} />
                  </span>
                  <span className="type-list__count">{t.count}</span>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
