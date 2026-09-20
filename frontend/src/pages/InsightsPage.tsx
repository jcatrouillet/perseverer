// Six sub-tabs, each owning its own data fetch and rendered only while active (so switching
// tabs is also what triggers that tab's otherwise-unneeded request): Pace trends (every run's
// VDOT plotted over its own date, see PaceTrendsChart.tsx), Training bands (share of running
// time spent at each pace, see TrainingBandsChart.tsx), Race predictions, Pace/HR Zones,
// VO2max (independently computed, never Garmin's own precomputed equivalents -- see
// performance_rollup.py's own docstring for the model, RacePredictionsChart.tsx/Vo2maxChart.tsx
// for the charts), and Eddington Number (a per-year running Eddington number, EddingtonChart.tsx
// -- computed client-side, no new backend endpoint, since it's a pure aggregate over the same
// full running-history fetch this page's own Pace trends tab already performs). Pace/HR Zones
// (replaced the old "Threshold Analysis" tab -- two point-in-time numbers with no ranges and no
// supporting evidence -- with a complete 5-zone pace+HR table built from the athlete's entire
// running history; see PaceHrZonesTable.tsx/pace_hr_zones.py). VO2max pairs its trend chart with
// a factor-analysis panel -- which workout(s) currently drive the value, what else qualified,
// what's missing -- on the same tab, since chart and breakdown are read together. The
// athlete-wide window/kind view that used to live here now lives per-activity instead (see
// ActivityInsightsPanel.tsx / rules_activity.py) -- this page is being rebuilt one category at a
// time.
import { useState } from "react";

import { useAllActivities } from "../api/queries";
import { EddingtonChart } from "../components/EddingtonChart";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { PaceHrZonesTable } from "../components/PaceHrZonesTable";
import { PaceTrendsChart } from "../components/PaceTrendsChart";
import { PerformanceCurveChart } from "../components/PerformanceCurveChart";
import { RacePredictionsChart } from "../components/RacePredictionsChart";
import { RaceReadinessChart } from "../components/RaceReadinessChart";
import { TrainingBandsChart } from "../components/TrainingBandsChart";
import { Vo2maxChart } from "../components/Vo2maxChart";
import { Vo2maxFactorAnalysis } from "../components/Vo2maxFactorAnalysis";
import "../styles/insights.css";

type Tab =
  | "pace-trends"
  | "training-bands"
  | "race-predictions"
  | "race-readiness"
  | "pace-hr-zones"
  | "vo2max"
  | "performance-curve"
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
          aria-selected={tab === "race-readiness"}
          className={tab === "race-readiness" ? "is-active" : undefined}
          onClick={() => setTab("race-readiness")}
        >
          Race Readiness
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "pace-hr-zones"}
          className={tab === "pace-hr-zones" ? "is-active" : undefined}
          onClick={() => setTab("pace-hr-zones")}
        >
          Pace/HR Zones
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
          aria-selected={tab === "performance-curve"}
          className={tab === "performance-curve" ? "is-active" : undefined}
          onClick={() => setTab("performance-curve")}
        >
          Performance Curve
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
      {tab === "race-readiness" && <RaceReadinessChart />}
      {tab === "pace-hr-zones" && <PaceHrZonesTable />}
      {tab === "vo2max" && (
        <>
          <Vo2maxChart />
          <Vo2maxFactorAnalysis />
        </>
      )}
      {tab === "performance-curve" && <PerformanceCurveChart />}
      {tab === "eddington" && <EddingtonChart />}
    </main>
  );
}
