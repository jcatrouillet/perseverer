// The per-route grade/status table for a bouldering activity -- reads ActivityDetail.splits
// (the raw FIT split_mesgs data, otherwise unused anywhere in the frontend) through
// boulderingRoutes.ts's reverse-engineered climb_grade/climb_result fields. See that module's
// own header comment for how the encoding was cracked.
import type { SplitOut } from "../api/types";
import {
  boulderingRoutes,
  formatGrade,
  formatResult,
  summarizeBoulderingRoutes,
} from "../boulderingRoutes";
import { formatClockDuration } from "../runningStats";

export function BoulderingRoutesTable({ splits }: { splits: SplitOut[] }) {
  const routes = boulderingRoutes(splits);
  if (routes.length === 0) return null;

  const { totalRoutes, completedRoutes } = summarizeBoulderingRoutes(routes);

  return (
    <section className="card">
      <h2>Routes</h2>
      <p className="bouldering-routes__caption">
        {completedRoutes} of {totalRoutes} route{totalRoutes === 1 ? "" : "s"} completed.
      </p>
      <div className="table-scroll">
        <table className="bouldering-routes-table">
          <thead>
            <tr>
              <th>Route</th>
              <th>Grade</th>
              <th>Status</th>
              <th>Duration</th>
              <th>Avg HR</th>
            </tr>
          </thead>
          <tbody>
            {routes.map((r) => (
              <tr
                key={r.routeNumber}
                className={
                  r.result === "completed"
                    ? "bouldering-routes-table__row--completed"
                    : undefined
                }
              >
                <td>{r.routeNumber}</td>
                <td>{formatGrade(r.grade)}</td>
                <td>{formatResult(r.result)}</td>
                <td>{r.durationS != null ? formatClockDuration(r.durationS) : "—"}</td>
                <td>{r.avgHr != null ? `${Math.round(r.avgHr)} bpm` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
