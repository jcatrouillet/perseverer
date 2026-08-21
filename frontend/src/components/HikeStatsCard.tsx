// A Month/Year view section for hiking, mirroring RunningStats' own "special-cased sport gets a
// dedicated section" precedent but at PeriodStatsCard's scale, not RunningStats' -- three summary
// tiles plus up to three featured hikes (longest by distance, by time, and by elevation gain,
// each skipped if it's the same activity as an already-featured one). Location comes from
// GET /activities/{id}/location per featured hike (see useActivityLocation's own docstring: it's
// a lazy, cached-forever, per-activity lookup -- there is no batch form), so exactly three calls
// are made unconditionally, one per featured-hike slot, `enabled` only when that slot is filled.
import { Link } from "wouter";

import { useActivityLocation } from "../api/queries";
import type { ActivitySummary } from "../api/types";
import { effectiveDurationS, formatDurationHM } from "../runningStats";
import { displayActivityName } from "../yearStats";
import { Icon } from "./Icon";
import { StatTile } from "./StatTile";
import "../styles/hike-stats.css";

interface FeaturedHike {
  label: string;
  activity: ActivitySummary;
}

/** Longest by distance, then longest by time and highest elevation gain -- each only included if
 * it's a *different* activity from everything already picked, per the same "don't repeat the
 * same hike under three labels" reasoning the caller asked for. Each criterion is computed only
 * over hikes that actually have that field (a null/0 distance or elevation_gain_m is real device
 * data, not a hike to feature as "longest"/"highest") -- so on a month where no hike recorded
 * elevation, that slot simply doesn't appear rather than crowning a 0m "highest gain". */
function pickFeaturedHikes(activities: ActivitySummary[]): FeaturedHike[] {
  const featured: FeaturedHike[] = [];
  const seen = new Set<string>();

  const byDistance = activities.filter((a) => (a.distance_m ?? 0) > 0);
  if (byDistance.length > 0) {
    const longest = byDistance.reduce((max, a) => (a.distance_m! > max.distance_m! ? a : max));
    featured.push({ label: "Longest hike", activity: longest });
    seen.add(longest.id);
  }

  const byDuration = activities.filter((a) => (effectiveDurationS(a) ?? 0) > 0);
  if (byDuration.length > 0) {
    const longestByTime = byDuration.reduce((max, a) =>
      effectiveDurationS(a)! > effectiveDurationS(max)! ? a : max,
    );
    if (!seen.has(longestByTime.id)) {
      featured.push({ label: "Longest hike by time", activity: longestByTime });
      seen.add(longestByTime.id);
    }
  }

  const byElevation = activities.filter((a) => (a.elevation_gain_m ?? 0) > 0);
  if (byElevation.length > 0) {
    const mostElevation = byElevation.reduce((max, a) =>
      a.elevation_gain_m! > max.elevation_gain_m! ? a : max,
    );
    if (!seen.has(mostElevation.id)) {
      featured.push({ label: "Highest elevation gain", activity: mostElevation });
      seen.add(mostElevation.id);
    }
  }

  return featured;
}

function FeaturedHikeCard({
  label,
  activity,
  locationName,
}: {
  label: string;
  activity: ActivitySummary;
  locationName?: string | null;
}) {
  const durationS = effectiveDurationS(activity);
  const title = displayActivityName(activity) ?? "Hike";
  return (
    <Link href={`/activities/${activity.id}`} className="hike-featured-card">
      <span className="hike-featured-card__label">
        <Icon name="trophy" /> {label}
      </span>
      <span className="hike-featured-card__title">{title}</span>
      <span className="hike-featured-card__meta">
        {activity.local_date ?? activity.start_time_utc.slice(0, 10)}
        {locationName && ` · ${locationName}`}
      </span>
      <span className="hike-featured-card__stats">
        {activity.distance_m != null && `${(activity.distance_m / 1000).toFixed(1)} km`}
        {durationS != null && ` · ${formatDurationHM(durationS)}`}
        {activity.elevation_gain_m != null &&
          activity.elevation_gain_m > 0 &&
          ` · +${Math.round(activity.elevation_gain_m)} m`}
      </span>
    </Link>
  );
}

export function HikeStatsCard({
  activities,
  periodLabel,
}: {
  activities: ActivitySummary[];
  periodLabel: string;
}) {
  const featured = pickFeaturedHikes(activities);

  // Fixed at exactly three hook calls regardless of how many featured slots are actually filled
  // (React's own rules-of-hooks -- the call count/order must never depend on data), gated by
  // `enabled` per slot. An empty id when a slot is unfilled never fires a request.
  const loc0 = useActivityLocation(featured[0]?.activity.id ?? "", featured[0] != null);
  const loc1 = useActivityLocation(featured[1]?.activity.id ?? "", featured[1] != null);
  const loc2 = useActivityLocation(featured[2]?.activity.id ?? "", featured[2] != null);
  const locations = [loc0, loc1, loc2];

  if (activities.length === 0) {
    return (
      <section className="card">
        <h2>Hikes</h2>
        <p>No hikes recorded in {periodLabel}.</p>
      </section>
    );
  }

  const totalDistanceM = activities.reduce((sum, a) => sum + (a.distance_m ?? 0), 0);
  const totalDurationS = activities.reduce((sum, a) => sum + (effectiveDurationS(a) ?? 0), 0);

  return (
    <section className="card">
      <h2>Hikes</h2>
      <div className="stat-grid">
        <StatTile
          label="Hikes"
          value={activities.length}
          icon="hike"
          tone="elevation"
          hero
        />
        <StatTile
          label="Total distance"
          value={(totalDistanceM / 1000).toFixed(1)}
          unit="km"
          icon="route"
          tone="elevation"
          hero
        />
        <StatTile
          label="Total time"
          value={formatDurationHM(totalDurationS)}
          icon="clock"
          tone="elevation"
          hero
        />
      </div>

      {featured.length > 0 && (
        <div className="hike-featured-grid">
          {featured.map((f, i) => (
            <FeaturedHikeCard
              key={f.activity.id}
              label={f.label}
              activity={f.activity}
              locationName={
                locations[i]?.data?.available ? locations[i]?.data?.location_name : null
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}
