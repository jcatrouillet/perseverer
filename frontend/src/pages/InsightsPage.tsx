// Two sub-tabs, each owning its own data fetch and rendered only while active (so switching
// tabs is also what triggers Training bands' otherwise-unneeded request): Pace trends (every
// run's VDOT plotted over its own date, see PaceTrendsChart.tsx) and Training bands (share of
// running time spent at each pace, see TrainingBandsChart.tsx). The athlete-wide window/kind
// view that used to live here now lives per-activity instead (see ActivityInsightsPanel.tsx /
// rules_activity.py) -- this page is being rebuilt one category at a time.
import { useState } from "react";

import { useAllActivities } from "../api/queries";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { PaceTrendsChart } from "../components/PaceTrendsChart";
import { TrainingBandsChart } from "../components/TrainingBandsChart";
import "../styles/insights.css";

type Tab = "pace-trends" | "training-bands";

export function InsightsPage() {
  const [tab, setTab] = useState<Tab>("pace-trends");
  const runs = useAllActivities({ sport: "running" });

  return (
    <main>
      <h1>Insights</h1>
      <div className="insights-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "pace-trends"}
          className={tab === "pace-trends" ? "is-active" : undefined}
          onClick={() => setTab("pace-trends")}
        >
          Pace trends
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "training-bands"}
          className={tab === "training-bands" ? "is-active" : undefined}
          onClick={() => setTab("training-bands")}
        >
          Training bands
        </button>
      </div>

      {tab === "pace-trends" && (
        <>
          {runs.isLoading && <LoadingSpinner />}
          {runs.isError && <p role="alert">Could not load running activities.</p>}
          {runs.data && <PaceTrendsChart activities={runs.data} />}
        </>
      )}
      {tab === "training-bands" && <TrainingBandsChart />}
    </main>
  );
}
