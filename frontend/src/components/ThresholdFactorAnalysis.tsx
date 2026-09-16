// Insights' Threshold Analysis tab: "which workout(s) led to the current threshold HR and pace"
// -- see threshold_analysis.py's own docstring for the model (GET /performance/threshold-
// analysis, a deliberate request-time exception to the rollup mandate, same as
// /activities/needs-trim). Both threshold PACES are pure functions of the same rolling_vdot
// VO2max already uses, so "which workout drives the threshold pace" reuses that same VO2max
// factor analysis rather than answering it twice. Threshold HR is different: the empirical path
// is a median over several qualifying runs (not one driving run), and the fallback path is
// driven by whichever activity set max_hr_bpm -- both rendered explicitly below rather than
// collapsed into a single number with no explanation.
import { Link } from "wouter";

import { useThresholdFactorAnalysis } from "../api/queries";
import type {
  ActivityRefOut,
  ThresholdHrBreakdownOut,
  ThresholdHrContributorOut,
  Vo2maxContributorOut,
} from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import { formatDurationHM } from "../runningStats";
import { Icon, type IconName } from "./Icon";
import { LoadingSpinner } from "./LoadingSpinner";
import "../styles/insights.css";

function ActivityCard({
  href,
  icon,
  tone,
  title,
  badge,
  meta,
}: {
  href: string;
  icon: IconName;
  tone: string;
  title: string;
  badge?: string;
  meta: string;
}) {
  return (
    <Link href={href} className="insight-card__link">
      <div className={`insight-card tone-${tone}`}>
        <span className="icon-chip">
          <Icon name={icon} />
        </span>
        <div className="insight-card__body">
          <span className="insight-card__title">
            {title}
            {badge && <span className="badge vo2max-factors__badge">{badge}</span>}
          </span>
          <span className="insight-card__date">{meta}</span>
        </div>
      </div>
    </Link>
  );
}

function Vo2maxDrivingCard({ contributor }: { contributor: Vo2maxContributorOut }) {
  const { formatDistance } = useDistanceFormat();
  return (
    <ActivityCard
      href={`/activities/${contributor.activity_id}`}
      icon="trophy"
      tone="pace"
      title={contributor.name ?? "Untitled activity"}
      badge="Sets your threshold pace"
      meta={
        `${contributor.local_date} · VDOT ${contributor.vdot.toFixed(1)} · ` +
        (contributor.distance_m == null ? "—" : formatDistance(contributor.distance_m, 2)) +
        (contributor.duration_s != null ? ` · ${formatDurationHM(contributor.duration_s)}` : "")
      }
    />
  );
}

function ThresholdHrContributorCard({
  contributor,
}: {
  contributor: ThresholdHrContributorOut;
}) {
  const { formatDistance, formatPace } = useDistanceFormat();
  return (
    <ActivityCard
      href={`/activities/${contributor.activity_id}`}
      icon="heart"
      tone={contributor.is_median ? "pace" : "neutral"}
      title={contributor.name ?? "Untitled activity"}
      badge={contributor.is_median ? "Sets the median" : undefined}
      meta={
        `${contributor.local_date} · ${contributor.avg_hr_bpm.toFixed(0)} bpm at ` +
        `${formatPace(contributor.pace_s_per_km)} · ` +
        (contributor.distance_m == null ? "—" : formatDistance(contributor.distance_m, 2)) +
        (contributor.duration_s != null ? ` · ${formatDurationHM(contributor.duration_s)}` : "")
      }
    />
  );
}

function MaxHrDrivingCard({ activity }: { activity: ActivityRefOut }) {
  const { formatDistance } = useDistanceFormat();
  return (
    <ActivityCard
      href={`/activities/${activity.activity_id}`}
      icon="heart"
      tone="hr"
      title={activity.name ?? "Untitled activity"}
      badge="Set your max HR"
      meta={
        `${activity.local_date} · ${activity.sport} · ` +
        (activity.distance_m == null ? "—" : formatDistance(activity.distance_m, 2)) +
        (activity.duration_s != null ? ` · ${formatDurationHM(activity.duration_s)}` : "")
      }
    />
  );
}

function ThresholdHrCard({
  title,
  breakdown,
}: {
  title: string;
  breakdown: ThresholdHrBreakdownOut;
}) {
  const { formatPace } = useDistanceFormat();
  return (
    <section className="card">
      <h2>{title}</h2>
      {breakdown.threshold_hr_bpm != null && (
        <p className="chart-note">
          Currently {breakdown.threshold_hr_bpm.toFixed(0)} bpm
          {breakdown.reference_pace_s_per_km != null &&
            ` at ${formatPace(breakdown.reference_pace_s_per_km)}`}
          {breakdown.threshold_hr_source === "empirical"
            ? " -- the median across the qualifying runs below."
            : " -- a fallback percentage of max HR, not enough qualifying runs yet."}
        </p>
      )}

      {breakdown.contributors.length > 0 && (
        <ul className="vo2max-factors__list">
          {breakdown.contributors.map((c) => (
            <li key={c.activity_id}>
              <ThresholdHrContributorCard contributor={c} />
            </li>
          ))}
        </ul>
      )}

      {breakdown.max_hr_driving_activity && (
        <ul className="vo2max-factors__list">
          <li>
            <MaxHrDrivingCard activity={breakdown.max_hr_driving_activity} />
          </li>
        </ul>
      )}

      {breakdown.missing.length > 0 && (
        <ul className="vo2max-factors__missing">
          {breakdown.missing.map((m) => (
            <li key={m} className="vo2max-factors__missing-item">
              {m}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ThresholdFactorAnalysis() {
  const analysis = useThresholdFactorAnalysis();

  if (analysis.isLoading) return <LoadingSpinner />;
  if (analysis.isError) {
    return <p role="alert">Could not load the threshold factor analysis.</p>;
  }
  if (!analysis.data) return null;

  const { vo2max, anaerobic_threshold_hr, aerobic_threshold_hr } = analysis.data;

  return (
    <>
      <section className="card">
        <h2>What's driving your threshold pace</h2>
        <p className="chart-note">
          Both threshold paces come from the same rolling VO2max estimate, currently{" "}
          {vo2max.window_start} to {vo2max.window_end} -- one run sets it, not an average across
          the window.
        </p>
        {vo2max.driving_activity ? (
          <ul className="vo2max-factors__list">
            <li>
              <Vo2maxDrivingCard contributor={vo2max.driving_activity} />
            </li>
          </ul>
        ) : (
          <p className="chart-note">No qualifying run in the current window.</p>
        )}
      </section>

      <ThresholdHrCard title="Anaerobic threshold HR" breakdown={anaerobic_threshold_hr} />
      <ThresholdHrCard title="Aerobic threshold HR" breakdown={aerobic_threshold_hr} />
    </>
  );
}
