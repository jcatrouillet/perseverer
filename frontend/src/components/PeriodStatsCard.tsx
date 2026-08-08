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

function formatClock(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.round((totalSeconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function PeriodStatsCard({
  title,
  activities,
  busiestLabel,
  busiestValue,
}: {
  title: string;
  activities: ActivitySummary[];
  busiestLabel: string;
  busiestValue: string | null;
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
            value={formatClock(avgDurationS)}
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
