// Per-km splits panel beside the detailed route map -- hovering a row highlights that segment on
// ActivityRouteMap (state lives one level up in ActivityRoute.tsx, which owns both). Columns
// match the reference image: KM, Pace (or GAP alongside it), Elev.
import type { KmSplit } from "../splits";
import { formatMinPerKm } from "../runningStats";

function formatElev(elevChangeM: number | null): string {
  if (elevChangeM == null) return "—";
  const rounded = Math.round(elevChangeM);
  if (rounded === 0) return "0 m";
  return rounded > 0 ? `+${rounded} m` : `${rounded} m`;
}

function formatPaceOrSpeed(paceMinPerKm: number, paceSport: boolean): string {
  return paceSport ? `${formatMinPerKm(paceMinPerKm)} /km` : `${(60 / paceMinPerKm).toFixed(1)} km/h`;
}

export function SplitsTable({
  splits,
  paceSport,
  hoveredKm,
  onHoverKm,
}: {
  splits: KmSplit[];
  /** Foot sports read as pace (min/km) with a GAP column; wheeled ones read as speed (km/h),
   * matching ActivityCard/ActivityCharts' own pace-vs-speed split. GAP is a running-specific
   * concept (gap.ts's own docstring), so it's only shown for pace sports. */
  paceSport: boolean;
  hoveredKm: number | null;
  onHoverKm: (km: number | null) => void;
}) {
  if (splits.length === 0) return null;

  return (
    <table className="splits-table">
      <thead>
        <tr>
          <th>KM</th>
          <th>{paceSport ? "Pace" : "Speed"}</th>
          {paceSport && <th>GAP</th>}
          <th>Elev</th>
        </tr>
      </thead>
      <tbody>
        {splits.map((split) => (
          <tr
            key={split.km}
            className={hoveredKm === split.km ? "splits-table__row--hovered" : ""}
            onMouseEnter={() => onHoverKm(split.km)}
            onMouseLeave={() => onHoverKm(null)}
          >
            <td>{split.km}</td>
            <td>{formatPaceOrSpeed(split.paceMinPerKm, paceSport)}</td>
            {paceSport && (
              <td>{split.gapMinPerKm != null ? formatPaceOrSpeed(split.gapMinPerKm, paceSport) : "—"}</td>
            )}
            <td>{formatElev(split.elevChangeM)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
