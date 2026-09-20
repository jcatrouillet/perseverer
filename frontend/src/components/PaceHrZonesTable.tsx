// Insights' "Pace/HR Zones" tab -- a complete 5-zone pace + heart-rate training table (Recovery/
// Basic Endurance/Aerobic Threshold/Lactate Threshold/VO2 Max), replacing the old "Threshold
// Analysis" tab's two single point-in-time numbers with something an athlete can actually train
// off of directly. See pace_hr_zones.py's own module docstring for the model, the literature it's
// built on, and why every number comes from the athlete's entire running history rather than a
// rolling "current fitness" window. Each zone's own qualifying runs (the literal answer to "why
// this range") are collapsed by default, same convention `ExerciseLibraryPage.tsx`'s categories
// and `BloodTestsPanel.tsx`'s panels already establish -- one open by default (Zone 2, since
// that's where most easy running actually happens) rather than all five expanded at once.
import { Link } from "wouter";

import { usePaceHrZones } from "../api/queries";
import type { PaceHrZoneOut, ZoneRunSampleOut } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import { formatDurationHM, formatMinPerKm } from "../runningStats";
import { Icon, type IconName } from "./Icon";
import { LoadingSpinner } from "./LoadingSpinner";
import "../styles/insights.css";
import "../styles/pace-hr-zones.css";

const ZONE_ICONS: readonly IconName[] = ["moon", "walk", "gauge", "flame", "bolt"];
const ZONE_TONES = ["neutral", "pace", "hr", "cadence", "power"] as const;

function usePaceRangeFormatter() {
  const { unitLabel, paceMinPerDisplayUnit } = useDistanceFormat();
  return (fast: number | null, slow: number | null): string => {
    const fastStr = fast != null ? formatMinPerKm(paceMinPerDisplayUnit(fast)) : null;
    const slowStr = slow != null ? formatMinPerKm(paceMinPerDisplayUnit(slow)) : null;
    if (fastStr != null && slowStr != null) return `${fastStr}–${slowStr} /${unitLabel}`;
    // Only a fast-side edge is defined (zone 1): that edge is the fastest this zone still
    // counts as, extending slower with no floor -- "slower than" it, not faster.
    if (fastStr != null) return `Slower than ${fastStr} /${unitLabel}`;
    // Only a slow-side edge is defined (zone 5): that edge is the slowest this zone still
    // counts as, extending faster with no ceiling -- "faster than" it.
    if (slowStr != null) return `Faster than ${slowStr} /${unitLabel}`;
    return "—";
  };
}

function hrRangeText(low: number | null, high: number | null): string {
  if (low != null && high != null) return `${low}–${high} bpm`;
  if (high != null) return `Below ${high} bpm`;
  if (low != null) return `Above ${low} bpm`;
  return "—";
}

function hrSourceNote(zone: PaceHrZoneOut): string | null {
  if (zone.hr_source === "empirical") {
    return `Based on the middle half (25th–75th percentile) of ${zone.qualifying_run_count} of your own runs at this pace.`;
  }
  if (zone.hr_source === "formula_fallback") {
    return zone.qualifying_run_count > 0
      ? `Only ${zone.qualifying_run_count} of your own runs at this pace so far (3+ needed) — estimated from your max heart rate instead.`
      : "No runs of yours at this pace yet — estimated from your max heart rate instead.";
  }
  return null;
}

function SampleRunRow({
  run,
  formatDistance,
}: {
  run: ZoneRunSampleOut;
  formatDistance: (m: number) => string;
}) {
  const { unitLabel, paceMinPerDisplayUnit } = useDistanceFormat();
  return (
    <li>
      <Link href={`/activities/${run.activity_id}`} className="insight-card__link">
        <div className="pace-hr-zones__run">
          <span className="pace-hr-zones__run-date">{run.local_date}</span>
          <span className="pace-hr-zones__run-name">{run.name ?? "Untitled activity"}</span>
          <span className="pace-hr-zones__run-stats">
            {formatMinPerKm(paceMinPerDisplayUnit(run.pace_s_per_km))} /{unitLabel} ·{" "}
            {run.avg_hr_bpm.toFixed(0)} bpm
            {run.distance_m != null && ` · ${formatDistance(run.distance_m)}`}
            {run.duration_s != null && ` · ${formatDurationHM(run.duration_s)}`}
          </span>
        </div>
      </Link>
    </li>
  );
}

function ZoneEvidence({ zone }: { zone: PaceHrZoneOut }) {
  const { formatDistance } = useDistanceFormat();
  const note = hrSourceNote(zone);
  return (
    <details className="pace-hr-zones__evidence" open={zone.number === 2}>
      <summary>
        Why these numbers{" "}
        <span className="pace-hr-zones__count">
          {zone.qualifying_run_count} run{zone.qualifying_run_count === 1 ? "" : "s"}
        </span>
      </summary>
      {note && <p className="chart-note">{note}</p>}
      {zone.sample_runs.length > 0 ? (
        <ul className="pace-hr-zones__run-list">
          {zone.sample_runs.map((r) => (
            <SampleRunRow key={r.activity_id} run={r} formatDistance={formatDistance} />
          ))}
        </ul>
      ) : (
        <p className="chart-note">No qualifying runs at this pace yet.</p>
      )}
    </details>
  );
}

export function PaceHrZonesTable() {
  const zones = usePaceHrZones();
  const formatRange = usePaceRangeFormatter();

  if (zones.isLoading) return <LoadingSpinner />;
  if (zones.isError) return <p role="alert">Could not load pace/HR zones.</p>;
  if (!zones.data) return null;

  const { profile_vdot, profile_vdot_activity, profile_max_hr_bpm, missing, zones: zoneList } =
    zones.data;

  return (
    <>
      <section className="card">
        <h2>Pace &amp; heart-rate training zones</h2>
        <p className="chart-note">
          Built from the best VDOT you've ever recorded
          {profile_vdot != null && ` (${profile_vdot.toFixed(1)})`}
          {profile_vdot_activity && (
            <>
              {" "}
              on{" "}
              <Link href={`/activities/${profile_vdot_activity.activity_id}`}>
                {profile_vdot_activity.local_date}
              </Link>
            </>
          )}
          {profile_max_hr_bpm != null &&
            ` and your own highest recorded heart rate (${profile_max_hr_bpm.toFixed(0)} bpm)`}
          {" "}
          — a stable reference table, not a day-to-day fitness tracker. See each zone's own
          "Why these numbers" below for the runs behind its heart-rate range.
        </p>
        {missing.length > 0 && (
          <ul className="vo2max-factors__missing">
            {missing.map((m) => (
              <li key={m} className="vo2max-factors__missing-item">
                {m}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <div className="table-scroll">
          <table className="pace-hr-zones__table">
            <thead>
              <tr>
                <th>Zone</th>
                <th>Pace</th>
                <th>Heart rate</th>
                <th>When &amp; how to use it</th>
              </tr>
            </thead>
            <tbody>
              {zoneList.map((zone, i) => (
                <tr key={zone.number} className={`tone-${ZONE_TONES[i]}`}>
                  <td>
                    <span className="pace-hr-zones__zone-label">
                      <span className="icon-chip">
                        <Icon name={ZONE_ICONS[i]} />
                      </span>
                      Z{zone.number} {zone.label}
                    </span>
                  </td>
                  <td>{formatRange(zone.pace_fast_s_per_km, zone.pace_slow_s_per_km)}</td>
                  <td>{hrRangeText(zone.hr_low_bpm, zone.hr_high_bpm)}</td>
                  <td className="pace-hr-zones__description">{zone.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {zoneList.map((zone) => (
        <section className="card" key={zone.number}>
          <h3>
            Z{zone.number} {zone.label}
          </h3>
          <ZoneEvidence zone={zone} />
        </section>
      ))}
    </>
  );
}
