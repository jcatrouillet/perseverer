// "The 30 fastest runs for the same distance, it should be way more compact" -- one line per
// row (pace, avg HR, date), no table chrome/header, reading GET /activities/{id}/context's
// `fastest` field (the same +/-15%-distance-band comparison pool ActivityContextStrip's
// percentile sentence draws on, just re-sorted pace-ascending and capped at 30). A sibling of
// ActivityContextStrip rather than folded into it -- one is a scatter/sparkline, this is a
// list, and neither needs the other's data shape.
//
// A per-row route sparkline was tried and dropped: at 30 rows, the extra width/height it needed
// to stay legible pushed the whole list well past the height of the stat-tile column it sits
// beside ("the table is too long... remove the run trace if needed but make it more compact") --
// bare text rows are the compact version the reference screenshot was actually asking for.
import { Link } from "wouter";

import type { ActivityContextRecentOut } from "../api/types";
import { formatMinPerKm, isPaceSport } from "../runningStats";

export function ActivityFastestTable({
  fastest,
  sport,
  currentActivityId,
}: {
  fastest: ActivityContextRecentOut[];
  sport: string;
  currentActivityId: string;
}) {
  const rows = fastest.filter((r) => r.distance_m > 0 && r.duration_s > 0);

  if (rows.length < 2) return null;

  const paceSport = isPaceSport(sport);
  const titleRow = rows.find((r) => r.id === currentActivityId) ?? rows[0]!;
  // Must match the backend's own bucket, floor(distance_m / 1000) -- see
  // routers/activities.py::get_activity_context's km_floor_m. Math.round would show "10 km"
  // for a 9.76km activity even though the actual comparison pool is the [9000, 10000) bucket.
  const distanceKmLabel = Math.floor(titleRow.distance_m / 1000);

  return (
    <section className="card activity-fastest">
      <h2>
        Fastest {distanceKmLabel} km runs
      </h2>
      <ul className="activity-fastest__list">
        {rows.map((r) => {
          const distanceKm = r.distance_m / 1000;
          const value = paceSport
            ? r.duration_s / 60 / distanceKm
            : distanceKm / (r.duration_s / 3600);
          const isCurrent = r.id === currentActivityId;
          return (
            <li key={r.id}>
              <Link
                href={`/activities/${r.id}`}
                className={`activity-fastest__row${isCurrent ? " activity-fastest__row--current" : ""}`}
              >
                <span className="activity-fastest__pace">
                  {paceSport ? `${formatMinPerKm(value)}/km` : `${value.toFixed(1)}km/h`}
                </span>
                {r.avg_hr_bpm != null && (
                  <span className="activity-fastest__hr">{Math.round(r.avg_hr_bpm)}bpm</span>
                )}
                <span className="activity-fastest__date">{r.local_date ?? "—"}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
