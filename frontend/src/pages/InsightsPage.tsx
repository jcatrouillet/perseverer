// Four sub-tabs, each owning its own data fetch and rendered only while active (so switching
// tabs is also what triggers that tab's otherwise-unneeded request): Pace trends (every run's
// VDOT plotted over its own date, see PaceTrendsChart.tsx), Training bands (share of running
// time spent at each pace, see TrainingBandsChart.tsx), Race predictions and Threshold & Max HR
// (both independently computed, never Garmin's own precomputed equivalents -- see
// performance_rollup.py's own docstring for the model, RacePredictionsChart.tsx/
// ThresholdMaxHrChart.tsx for the charts). The athlete-wide window/kind view that used to live
// here now lives per-activity instead (see ActivityInsightsPanel.tsx / rules_activity.py) --
// this page is being rebuilt one category at a time.
import { useState } from "react";

import { useAllActivities } from "../api/queries";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { PaceTrendsChart } from "../components/PaceTrendsChart";
import { RacePredictionsChart } from "../components/RacePredictionsChart";
import { ThresholdMaxHrChart } from "../components/ThresholdMaxHrChart";
import { TrainingBandsChart } from "../components/TrainingBandsChart";
import "../styles/insights.css";

type Tab = "pace-trends" | "training-bands" | "race-predictions" | "threshold-max-hr";

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
        <button
          type="button"
          role="tab"
          aria-selected={tab === "race-predictions"}
          className={tab === "race-predictions" ? "is-active" : undefined}
          onClick={() => setTab("race-predictions")}
        >
          Race predictions
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "threshold-max-hr"}
          className={tab === "threshold-max-hr" ? "is-active" : undefined}
          onClick={() => setTab("threshold-max-hr")}
        >
          Threshold &amp; Max HR
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
      {tab === "race-predictions" && <RacePredictionsChart />}
      {tab === "threshold-max-hr" && <ThresholdMaxHrChart />}
    </main>
  );
}
