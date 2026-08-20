// First category: Pace trends (every run's VDOT plotted over its own date, see
// PaceTrendsChart.tsx). The athlete-wide window/kind view that used to live here now lives
// per-activity instead (see ActivityInsightsPanel.tsx / rules_activity.py) -- this page is
// being rebuilt one category at a time, Pace trends is the first.
import { useAllActivities } from "../api/queries";
import { PaceTrendsChart } from "../components/PaceTrendsChart";
import { TrainingBandsChart } from "../components/TrainingBandsChart";

export function InsightsPage() {
  const runs = useAllActivities({ sport: "running" });

  return (
    <main>
      <h1>Insights</h1>
      {runs.isLoading && <p>Loading…</p>}
      {runs.isError && <p role="alert">Could not load running activities.</p>}
      {runs.data && <PaceTrendsChart activities={runs.data} />}
      {runs.data && <TrainingBandsChart activities={runs.data} />}
    </main>
  );
}
