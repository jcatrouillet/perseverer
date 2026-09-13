// Six sub-tabs, each owning its own data fetch and rendered only while active (so switching
// tabs is also what triggers that tab's otherwise-unneeded request): Pace trends (every run's
// VDOT plotted over its own date, see PaceTrendsChart.tsx), Training bands (share of running
// time spent at each pace, see TrainingBandsChart.tsx), Race predictions, Threshold Analysis,
// VO2max (all three independently computed, never Garmin's own precomputed equivalents -- see
// performance_rollup.py's own docstring for the model, RacePredictionsChart.tsx/
// ThresholdAnalysisChart.tsx/Vo2maxChart.tsx for the charts), and Eddington Number (a per-year
// running Eddington number, EddingtonChart.tsx -- computed client-side, no new backend endpoint,
// since it's a pure aggregate over the same full running-history fetch this page's own Pace
// trends tab already performs). Threshold Analysis (renamed from "Threshold & Max HR" once it
// grew to cover both the anaerobic and the aerobic threshold, plus the factor-analysis panel
// below) and VO2max both pair their trend chart with a factor-analysis panel -- which workout(s)
// currently drive the value, what else qualified, what's missing -- on the same tab, since chart
// and breakdown are read together. The athlete-wide window/kind view that used to live here now
// lives per-activity instead (see ActivityInsightsPanel.tsx / rules_activity.py) -- this page is
// being rebuilt one category at a time.
import { useState } from "react";

import { useAllActivities } from "../api/queries";
import { EddingtonChart } from "../components/EddingtonChart";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { PaceTrendsChart } from "../components/PaceTrendsChart";
import { RacePredictionsChart } from "../components/RacePredictionsChart";
import { ThresholdAnalysisChart } from "../components/ThresholdAnalysisChart";
import { ThresholdFactorAnalysis } from "../components/ThresholdFactorAnalysis";
import { TrainingBandsChart } from "../components/TrainingBandsChart";
import { Vo2maxChart } from "../components/Vo2maxChart";
import { Vo2maxFactorAnalysis } from "../components/Vo2maxFactorAnalysis";
import "../styles/insights.css";

type Tab =
  | "pace-trends"
  | "training-bands"
  | "race-predictions"
  | "threshold-analysis"
  | "vo2max"
  | "eddington";

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
          aria-selected={tab === "threshold-analysis"}
          className={tab === "threshold-analysis" ? "is-active" : undefined}
          onClick={() => setTab("threshold-analysis")}
        >
          Threshold Analysis
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "vo2max"}
          className={tab === "vo2max" ? "is-active" : undefined}
          onClick={() => setTab("vo2max")}
        >
          VO2max
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "eddington"}
          className={tab === "eddington" ? "is-active" : undefined}
          onClick={() => setTab("eddington")}
        >
          Eddington Number
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
      {tab === "threshold-analysis" && (
        <>
          <ThresholdAnalysisChart />
          <ThresholdFactorAnalysis />
        </>
      )}
      {tab === "vo2max" && (
        <>
          <Vo2maxChart />
          <Vo2maxFactorAnalysis />
        </>
      )}
      {tab === "eddington" && <EddingtonChart />}
    </main>
  );
}
