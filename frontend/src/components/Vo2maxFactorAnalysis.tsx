// Insights' VO2max tab: "which activities contributed to this value, and what's missing" --
// see vo2max_analysis.py's own docstring for the window/expiry/staleness model this reads
// (GET /performance/vo2max-analysis, a deliberate request-time exception to the rollup mandate,
// same as /activities/needs-trim). `rolling_vdot` is a rolling *maximum*, not an average, so
// exactly one run sets the current value -- everything else in the window only "contributes" in
// the sense of being ready to take over once that run ages out, which the copy below is careful
// to say rather than implying they're jointly averaged in.
import { Link } from "wouter";

import { useVo2maxFactorAnalysis } from "../api/queries";
import type { Vo2maxContributorOut } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import { formatDurationHM } from "../runningStats";
import { Icon } from "./Icon";
import { LoadingSpinner } from "./LoadingSpinner";
import "../styles/insights.css";

function ContributorRow({
  contributor,
  isDriving,
}: {
  contributor: Vo2maxContributorOut;
  isDriving: boolean;
}) {
  const tone = isDriving ? "pace" : "neutral";
  const { formatDistance } = useDistanceFormat();
  return (
    <Link href={`/activities/${contributor.activity_id}`} className="insight-card__link">
      <div className={`insight-card tone-${tone}`}>
        <span className="icon-chip">
          <Icon name={isDriving ? "trophy" : "trend"} />
        </span>
        <div className="insight-card__body">
          <span className="insight-card__title">
            {contributor.name ?? "Untitled activity"}
            {isDriving && <span className="badge vo2max-factors__badge">Current best</span>}
          </span>
          <span className="insight-card__date">
            {contributor.local_date} · VDOT {contributor.vdot.toFixed(1)} ·{" "}
            {contributor.distance_m == null ? "—" : formatDistance(contributor.distance_m, 2)}
            {contributor.duration_s != null && ` · ${formatDurationHM(contributor.duration_s)}`}
          </span>
        </div>
      </div>
    </Link>
  );
}

export function Vo2maxFactorAnalysis() {
  const analysis = useVo2maxFactorAnalysis();

  if (analysis.isLoading) return <LoadingSpinner />;
  if (analysis.isError) return <p role="alert">Could not load the VO2max factor analysis.</p>;
  if (!analysis.data) return null;

  const { driving_activity, other_contributors, missing, window_start, window_end } = analysis.data;

  return (
    <section className="card">
      <h2>What's driving this value</h2>
      <p className="chart-note">
        VO2max is the single best qualifying run in a trailing window, currently {window_start} to{" "}
        {window_end} -- one run sets it, not an average across the window.
      </p>

      {driving_activity ? (
        <ul className="vo2max-factors__list">
          <li>
            <ContributorRow contributor={driving_activity} isDriving />
          </li>
          {other_contributors.map((c) => (
            <li key={c.activity_id}>
              <ContributorRow contributor={c} isDriving={false} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="chart-note">No qualifying run in the current window.</p>
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
  );
}
