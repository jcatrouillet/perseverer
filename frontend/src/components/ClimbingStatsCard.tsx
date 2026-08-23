// A Week/Month/Year/AllTime view section for bouldering, mirroring HikeStatsCard's own
// "special-cased sport gets a dedicated section" precedent, including that precedent's
// presentational shape -- the page owns the useClimbingSummary(startDate, endDate) query
// (GET /activities/climbing-summary) and passes the result down, rather than this card fetching
// internally, matching HikeStatsCard (activities as a prop) and the lesson learned from
// BoulderingRoutesTable: no tested component in this codebase calls a React Query hook itself.
// Backed by a real server-side aggregate rather than a client-side reduction over an
// already-fetched ActivitySummary[] because the per-grade attempted/completed chart needs the
// full distribution across however many sessions fall in the period, not just a few scalar
// totals (see that endpoint's own docstring).
import type { ClimbingSummaryOut } from "../api/types";
import { formatGrade } from "../boulderingRoutes";
import { formatDurationHM } from "../runningStats";
import { ClimbGradeChart } from "./ClimbGradeChart";
import { StatTile } from "./StatTile";

export function ClimbingStatsCard({ summary }: { summary: ClimbingSummaryOut | undefined }) {
  // No bouldering sessions at all this period -- the section simply doesn't appear, same
  // reasoning as HikeStatsCard's own empty-state handling (bouldering, like hiking, is
  // occasional enough that an empty-state card on most periods would be pure noise).
  if (!summary || summary.session_count === 0) return null;

  const { session_count, total_climb_time_s, total_routes, max_completed_grade, grade_breakdown } =
    summary;

  return (
    <section className="card">
      <h2>Climbing</h2>
      <div className="stat-grid">
        <StatTile label="Sessions" value={session_count} icon="climb" tone="elevation" hero />
        <StatTile
          label="Climb time"
          value={formatDurationHM(total_climb_time_s)}
          icon="clock"
          tone="elevation"
          hero
        />
        <StatTile label="Routes" value={total_routes} icon="route" tone="elevation" hero />
        {max_completed_grade != null && (
          <StatTile
            label="Max grade completed"
            value={formatGrade(max_completed_grade)}
            icon="mountain"
            tone="elevation"
          />
        )}
      </div>
      <ClimbGradeChart gradeBreakdown={grade_breakdown} />
    </section>
  );
}
