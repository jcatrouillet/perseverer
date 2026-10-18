// Insights' "Pace/HR Zones" tab -- a complete 5-zone pace + heart-rate training table (Recovery/
// Basic Endurance/Aerobic Threshold/Lactate Threshold/VO2 Max), replacing the old "Threshold
// Analysis" tab's two single point-in-time numbers with something an athlete can actually train
// off of directly. See pace_hr_zones.py's own module docstring (especially its "How far back"
// section) for the model, the literature it's built on, and why the profile behind every number
// is windowed (two years for a race/max HR, one year for a training run) rather than the athlete's
// literal entire history or a short rolling "current fitness" window. Each zone's own qualifying
// runs (the literal answer to "why this range") are collapsed by default, same convention
// `ExerciseLibraryPage.tsx`'s categories and `BloodTestsPanel.tsx`'s panels already establish --
// one open by default (Zone 2, since that's where most easy running actually happens) rather than
// all five expanded at once.
//
// With the Insights switch on Cycling (`sport="cycling"`), the same five heart-rate zones for
// rides: no VDOT and no pace, but each zone's speed range from your own rides at that heart rate
// (pace_hr_zones.py::compute_ride_hr_zones).
import { Link } from "wouter";

import { usePaceHrZones } from "../api/queries";
import type { InsightsSport, PaceHrZoneOut, ZoneRunSampleOut } from "../api/types";
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

/** A ride zone's speed range (m/s in, km/h or mph out); "—" when too few rides qualify. */
function useSpeedRangeFormatter() {
  const { kmhToDisplay, speedUnitLabel } = useDistanceFormat();
  return (low: number | null | undefined, high: number | null | undefined): string => {
    if (low == null || high == null) return "—";
    const lo = kmhToDisplay(low * 3.6).toFixed(1);
    const hi = kmhToDisplay(high * 3.6).toFixed(1);
    return `${lo}–${hi} ${speedUnitLabel}`;
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
  const { unitLabel, paceMinPerDisplayUnit, kmhToDisplay, speedUnitLabel } = useDistanceFormat();
  return (
    <li>
      <Link href={`/activities/${run.activity_id}`} className="insight-card__link">
        <div className="pace-hr-zones__run">
          <span className="pace-hr-zones__run-date">{run.local_date}</span>
          <span className="pace-hr-zones__run-name">{run.name ?? "Untitled activity"}</span>
          <span className="pace-hr-zones__run-stats">
            {run.speed_mps != null
              ? `${kmhToDisplay(run.speed_mps * 3.6).toFixed(1)} ${speedUnitLabel}`
              : `${formatMinPerKm(paceMinPerDisplayUnit(run.pace_s_per_km))} /${unitLabel}`}{" "}
            · {run.avg_hr_bpm.toFixed(0)} bpm
            {run.distance_m != null && ` · ${formatDistance(run.distance_m)}`}
            {run.duration_s != null && ` · ${formatDurationHM(run.duration_s)}`}
          </span>
        </div>
      </Link>
    </li>
  );
}

function ZoneEvidence({ zone, sport }: { zone: PaceHrZoneOut; sport: InsightsSport }) {
  const { formatDistance } = useDistanceFormat();
  const cycling = sport === "cycling";
  const one = cycling ? "ride" : "run";
  const note = cycling
    ? zone.qualifying_run_count >= 3
      ? `Speed range: the middle half (25th–75th percentile) of the average speeds of these ${zone.qualifying_run_count} rides.`
      : "Fewer than 3 of your rides in the last 12 months sit at this heart rate, so no speed range yet."
    : hrSourceNote(zone);
  return (
    <details className="pace-hr-zones__evidence" open={zone.number === 2}>
      <summary>
        Why these numbers{" "}
        <span className="pace-hr-zones__count">
          {zone.qualifying_run_count} {one}
          {zone.qualifying_run_count === 1 ? "" : "s"}
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
        <p className="chart-note">
          {cycling ? "No rides at this heart rate yet." : "No qualifying runs at this pace yet."}
        </p>
      )}
    </details>
  );
}

export function PaceHrZonesTable({
  sport = "running",
  excludeCommutes = false,
}: {
  sport?: InsightsSport;
  excludeCommutes?: boolean;
}) {
  const zones = usePaceHrZones(undefined, sport, excludeCommutes);
  const formatRange = usePaceRangeFormatter();
  const formatSpeedRange = useSpeedRangeFormatter();
  const cycling = sport === "cycling";

  if (zones.isLoading) return <LoadingSpinner />;
  if (zones.isError) {
    return <p role="alert">Could not load {cycling ? "HR zones" : "pace/HR zones"}.</p>;
  }
  if (!zones.data) return null;

  const {
    profile_vdot,
    profile_vdot_activity,
    profile_vdot_source,
    profile_max_hr_bpm,
    missing,
    zones: zoneList,
  } = zones.data;
  const vdotOrigin =
    profile_vdot_source === "race"
      ? "your best race in the last 2 years"
      : "your best training run in the last year";

  return (
    <>
      <section className="card">
        <h2>
          {cycling ? "Heart-rate training zones for rides" : "Pace & heart-rate training zones"}
        </h2>
        {cycling ? (
          <p className="chart-note">
            Heart-rate zones from your highest recorded heart rate in the last 2 years
            {profile_max_hr_bpm != null && ` (${profile_max_hr_bpm.toFixed(0)} bpm)`}, with the
            speed you actually ride at in each one: the middle half of the average speeds of your
            rides in the last 12 months whose average heart rate falls in that zone. Terrain, wind
            and indoor rides all move speed, so read it as your own typical range, not a target.
          </p>
        ) : (
          <p className="chart-note">
            Built from the best VDOT you've recorded
            {profile_vdot != null && ` (${profile_vdot.toFixed(1)})`}, from {vdotOrigin},
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
              ` and your highest recorded heart rate in the last 2 years (${profile_max_hr_bpm.toFixed(0)} bpm)`}{" "}
            — a reference table that follows your current fitness, not a day-to-day tracker. See
            each zone's own "Why these numbers" below for the runs behind its heart-rate range.
          </p>
        )}
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
                <th>{cycling ? "Speed" : "Pace"}</th>
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
                  <td>
                    {cycling
                      ? formatSpeedRange(zone.speed_low_mps, zone.speed_high_mps)
                      : formatRange(zone.pace_fast_s_per_km, zone.pace_slow_s_per_km)}
                  </td>
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
          <ZoneEvidence zone={zone} sport={sport} />
        </section>
      ))}
    </>
  );
}
