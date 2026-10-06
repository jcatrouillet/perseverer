// "Add a comparison with the bouldering sessions of the same duration (climb + rest), compare #
// of routes, max completed grade and climb time" -- reads GET /activities/{id}/climb-comparisons
// (api/routers/activities.py::get_activity_climb_comparisons), the bouldering analogue of
// ActivityComparisonTable's own running comparison, matched on total session duration instead of
// distance+location (bouldering has neither).
import { Link } from "wouter";

import type { ActivityDetail, ClimbComparisonsOut } from "../api/types";
import { boulderingRoutes, climbSummary, formatGrade } from "../boulderingRoutes";
import { effectiveDurationS, formatClockDuration } from "../runningStats";

interface ComparisonRow {
  id: string;
  local_date: string | null;
  duration_s: number;
  route_count: number;
  max_completed_grade: number | null;
  climb_time_s: number | null;
  isCurrent: boolean;
}

export function ClimbComparisonTable({
  activity,
  comparisons,
}: {
  activity: ActivityDetail;
  comparisons: ClimbComparisonsOut;
}) {
  if (comparisons.rows.length === 0) return null;

  const currentDurationS = effectiveDurationS(activity);
  if (currentDurationS == null) return null;

  const currentClimb = climbSummary(boulderingRoutes(activity.splits), activity.splits);
  const currentRow: ComparisonRow = {
    id: activity.id,
    local_date: activity.local_date,
    duration_s: currentDurationS,
    route_count: currentClimb.routeCount,
    max_completed_grade: currentClimb.maxCompletedGrade,
    climb_time_s: currentClimb.climbTimeS,
    isCurrent: true,
  };
  const rows: ComparisonRow[] = [
    currentRow,
    ...comparisons.rows.map((r) => ({ ...r, isCurrent: false })),
  ];

  const bandPct = Math.round(comparisons.duration_band_fraction * 100);

  return (
    <section className="card">
      <h2>Similar sessions from here</h2>
      <p className="activity-comparison__caption">
        {comparisons.matched_count === comparisons.rows.length
          ? `${comparisons.matched_count} `
          : `${comparisons.rows.length} of ${comparisons.matched_count} `}
        session{comparisons.matched_count === 1 ? "" : "s"} within {bandPct}% of this session's own
        length.
      </p>
      <div className="table-scroll">
        <table className="comparison-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Duration</th>
              <th>Routes</th>
              <th>Max Grade</th>
              <th>Climb Time</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const rowClass = r.isCurrent
                ? "comparison-table__row comparison-table__row--current"
                : "comparison-table__row";
              const dateCell = r.local_date ?? "—";
              return (
                <tr key={r.id} className={rowClass}>
                  <td>
                    {r.isCurrent ? (
                      <span>{dateCell} (this session)</span>
                    ) : (
                      <Link href={`/activities/${r.id}`}>{dateCell}</Link>
                    )}
                  </td>
                  <td>{formatClockDuration(r.duration_s)}</td>
                  <td>{r.route_count}</td>
                  <td>
                    {r.max_completed_grade != null ? formatGrade(r.max_completed_grade) : "—"}
                  </td>
                  <td>{r.climb_time_s != null ? formatClockDuration(r.climb_time_s) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
