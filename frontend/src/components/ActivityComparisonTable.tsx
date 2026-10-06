// "Find runs about the same distance, the 10 most recent ones, with the same start run location,
// and see how the activity is doing in terms of VDOT, pace, GAP, heart rate, cadence" -- reads
// GET /activities/{id}/comparisons (api/routers/activities.py::get_activity_comparisons), the
// same "deliberately small, honest comparison view" posture as ActivityContextStrip/
// ActivityFastestTable, just scoped to "same place, same distance" instead of "same distance
// anywhere" or "a 90-day window". A real `<table>` (not ActivityFastestTable's bare list) since
// this has five real data columns per row, not one.
import { Link } from "wouter";

import type { ActivityComparisonsOut, ActivityDetail } from "../api/types";
import { metricValue } from "../activityMetrics";
import {
  kmhToDisplaySpeed,
  metersToDisplayDistance,
  paceMinPerDisplayUnit,
  speedUnitLabel,
  useDistanceFormat,
} from "../formatDistance";
import { effectiveDurationS, formatMinPerKm, gapPaceMinPerKm, isPaceSport } from "../runningStats";

// Mirrors gap.py::AVG_GAP_METRIC_KEY and routers/activities.py::CADENCE_METRIC_KEY exactly --
// duplicated string literals, not a shared constant module, matching how ActivityStatsGrid.tsx
// already reads every other fit.session.* key directly (ADR 0012's cross-module precedent).
const AVG_GAP_METRIC_KEY = "perseverer.performance.avg_gap_speed_mps";
const CADENCE_METRIC_KEY = "fit.session.avg_running_cadence";

interface ComparisonRow {
  id: string;
  local_date: string | null;
  distance_m: number;
  duration_s: number;
  vdot: number | null;
  avg_gap_speed_mps: number | null;
  avg_hr_bpm: number | null;
  avg_cadence_spm: number | null;
  isCurrent: boolean;
}

export function ActivityComparisonTable({
  activity,
  comparisons,
  sport,
}: {
  activity: ActivityDetail;
  comparisons: ActivityComparisonsOut;
  sport: string;
}) {
  if (comparisons.rows.length === 0) return null;

  const currentDurationS = effectiveDurationS(activity);
  if (currentDurationS == null || activity.distance_m == null) return null;

  const currentRow: ComparisonRow = {
    id: activity.id,
    local_date: activity.local_date,
    distance_m: activity.distance_m,
    duration_s: currentDurationS,
    vdot: activity.vdot,
    avg_gap_speed_mps: metricValue(activity.metrics, AVG_GAP_METRIC_KEY),
    avg_hr_bpm: activity.avg_hr_bpm,
    avg_cadence_spm: (() => {
      const raw = metricValue(activity.metrics, CADENCE_METRIC_KEY);
      return raw != null ? raw * 2 : null;
    })(),
    isCurrent: true,
  };
  const rows: ComparisonRow[] = [
    currentRow,
    ...comparisons.rows.map((r) => ({ ...r, isCurrent: false })),
  ];

  const paceSport = isPaceSport(sport);
  const { unit, unitLabel, metersToDisplay } = useDistanceFormat();
  const radiusLabel =
    comparisons.start_radius_m >= 1000
      ? `${metersToDisplay(comparisons.start_radius_m).toFixed(1)}${unitLabel}`
      : `${Math.round(comparisons.start_radius_m)}m`;
  const bandPct = Math.round(comparisons.distance_band_fraction * 100);

  return (
    <section className="card">
      <h2>Similar runs from here</h2>
      <p className="activity-comparison__caption">
        {comparisons.matched_count === comparisons.rows.length
          ? `${comparisons.matched_count} `
          : `${comparisons.rows.length} of ${comparisons.matched_count} `}
        run{comparisons.matched_count === 1 ? "" : "s"} within {bandPct}% of this distance, starting
        within {radiusLabel}.
      </p>
      <div className="table-scroll">
        <table className="comparison-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Distance</th>
              <th>{paceSport ? "Pace" : "Speed"}</th>
              <th>GAP</th>
              <th>VDOT</th>
              <th>Avg HR</th>
              <th>Cadence</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const distanceKm = r.distance_m / 1000;
              const paceValue = paceSport
                ? paceMinPerDisplayUnit(r.duration_s / distanceKm, unit)
                : kmhToDisplaySpeed(distanceKm / (r.duration_s / 3600), unit);
              const rowClass = r.isCurrent
                ? "comparison-table__row comparison-table__row--current"
                : "comparison-table__row";
              const dateCell = r.local_date ?? "—";
              return (
                <tr key={r.id} className={rowClass}>
                  <td>
                    {r.isCurrent ? (
                      <span>{dateCell} (this run)</span>
                    ) : (
                      <Link href={`/activities/${r.id}`}>{dateCell}</Link>
                    )}
                  </td>
                  <td>
                    {metersToDisplayDistance(r.distance_m, unit).toFixed(2)} {unitLabel}
                  </td>
                  <td>
                    {paceSport
                      ? `${formatMinPerKm(paceValue)}/${unitLabel}`
                      : `${paceValue.toFixed(1)}${speedUnitLabel(unit)}`}
                  </td>
                  <td>
                    {r.avg_gap_speed_mps != null
                      ? `${formatMinPerKm(paceMinPerDisplayUnit(gapPaceMinPerKm(r.avg_gap_speed_mps) * 60, unit))}/${unitLabel}`
                      : "—"}
                  </td>
                  <td>{r.vdot != null ? r.vdot.toFixed(1) : "—"}</td>
                  <td>{r.avg_hr_bpm != null ? `${Math.round(r.avg_hr_bpm)} bpm` : "—"}</td>
                  <td>
                    {r.avg_cadence_spm != null ? `${Math.round(r.avg_cadence_spm)} spm` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
