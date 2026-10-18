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
import { SportSectionTitle } from "./SportSectionTitle";
import { StatTile } from "./StatTile";

export function ClimbingStatsCard({ summary }: { summary: ClimbingSummaryOut | undefined }) {
  // No bouldering sessions at all this period -- the section simply doesn't appear, same
  // reasoning as HikeStatsCard's own empty-state handling (bouldering, like hiking, is
  // occasional enough that an empty-state card on most periods would be pure noise).
  if (!summary || summary.session_count === 0) return null;

  const {
    session_count,
    total_climb_time_s,
    timed_session_count,
    total_routes,
    max_completed_grade,
    grade_breakdown,
  } = summary;
  // A session logged only in Kaya (no watch recording) has routes but no time: unknown, never 0.
  const climbTimeMeta =
    total_climb_time_s == null
      ? "not recorded (Kaya only)"
      : timed_session_count < session_count
        ? `${timed_session_count} of ${session_count} sessions timed`
        : null;

  return (
    <section className="card">
      <SportSectionTitle sport="rock_climbing">Climbing</SportSectionTitle>
      <div className="stat-grid">
        <StatTile label="Sessions" value={session_count} icon="climb" tone="load" hero />
        <StatTile
          label="Climb time"
          value={total_climb_time_s != null ? formatDurationHM(total_climb_time_s) : "—"}
          meta={climbTimeMeta}
          icon="clock"
          tone="load"
          hero
        />
        <StatTile label="Routes" value={total_routes} icon="route" tone="load" hero />
        {max_completed_grade != null && (
          <StatTile
            label="Max grade completed"
            value={formatGrade(max_completed_grade)}
            icon="mountain"
            tone="load"
          />
        )}
      </div>
      <ClimbGradeChart gradeBreakdown={grade_breakdown} />
    </section>
  );
}
