// A Running/Cycling switch (default: Settings -> Personalize -> Insights open on) picks the sport
// every tab describes; runs are only compared with runs and rides with rides (insightsSport.ts).
// PR progress reuses the running-history query: one highest-VDOT run per ISO week,
// exact-distance pace frontiers comparing the past year with older records.
// Insights sub-tabs, each owning its own data fetch and rendered only while active (so switching
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
import type { InsightsSport } from "../api/types";
import { CyclingPowerPanel } from "../components/CyclingPowerPanel";
import { EddingtonChart } from "../components/EddingtonChart";
import { Icon } from "../components/Icon";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { PaceHrZonesTable } from "../components/PaceHrZonesTable";
import { PrProgressChart } from "../components/PrProgressChart";
import { PaceTrendsChart } from "../components/PaceTrendsChart";
import { PerformanceCurveChart } from "../components/PerformanceCurveChart";
import { RacePredictionsChart } from "../components/RacePredictionsChart";
import { RaceReadinessChart } from "../components/RaceReadinessChart";
import { PowerTrendsChart } from "../components/PowerTrendsChart";
import { TrainingBandsChart } from "../components/TrainingBandsChart";
import { Vo2maxChart } from "../components/Vo2maxChart";
import { Vo2maxFactorAnalysis } from "../components/Vo2maxFactorAnalysis";
import { INSIGHTS_SPORTS, type InsightsTab, tabAvailable, tabLabel } from "../insightsSport";
import { sportStyle } from "../metricStyle";
import { usePersonalize } from "../PersonalizeContext";
import "../styles/insights.css";

const TABS: readonly InsightsTab[] = [
  "pace-trends",
  "training-bands",
  "pr-progress",
  "race-predictions",
  "race-readiness",
  "pace-hr-zones",
  "vo2max",
  "performance-curve",
  "eddington",
];

const EXCLUDE_COMMUTES_KEY = "perseverer_exclude_commutes";

// A per-viewer convenience (like the theme), not an account setting: stays on across visits.
function readExcludeCommutes(): boolean {
  try {
    return localStorage.getItem(EXCLUDE_COMMUTES_KEY) === "1";
  } catch {
    return false;
  }
}

export function InsightsPage() {
  const { insights_sport: defaultSport, commute_filter: commuteFilter } = usePersonalize();
  const [excludeCommutesChoice, setExcludeCommutesChoice] = useState(readExcludeCommutes);
  // The toggle only exists when Settings -> Personalize offers it; off means nothing is left out.
  const excludeCommutes = commuteFilter && excludeCommutesChoice;
  function chooseExcludeCommutes(next: boolean) {
    setExcludeCommutesChoice(next);
    try {
      localStorage.setItem(EXCLUDE_COMMUTES_KEY, next ? "1" : "0");
    } catch {
      // Private window or blocked storage: the choice just lasts this visit.
    }
  }
  // null until the switch is used, so the page follows the saved default (Settings ->
  // Personalize) even when that setting arrives after the first render.
  const [chosenSport, setChosenSport] = useState<InsightsSport | null>(null);
  const sport = chosenSport ?? defaultSport;
  const [chosenTab, setTab] = useState<InsightsTab>("pace-trends");
  // A running-only tab (VO2max, race predictions/readiness) falls back to the first tab when
  // the switch moves to cycling.
  const tab = tabAvailable(chosenTab, sport) ? chosenTab : "pace-trends";
  const copy = INSIGHTS_SPORTS[sport];
  const activities = useAllActivities({ sport: copy.apiSport });

  const activityList = (render: (data: NonNullable<typeof activities.data>) => React.ReactNode) => (
    <>
      {activities.isLoading && <LoadingSpinner />}
      {activities.isError && <p role="alert">Could not load your {copy.many}.</p>}
      {activities.data &&
        render(excludeCommutes ? activities.data.filter((a) => !a.is_commute) : activities.data)}
    </>
  );

  return (
    <main>
      <div className="insights-header">
        <h1>Insights</h1>
        <div className="insights-sport" role="group" aria-label="Sport">
          {(Object.keys(INSIGHTS_SPORTS) as InsightsSport[]).map((s) => {
            const style = sportStyle(INSIGHTS_SPORTS[s].apiSport);
            return (
              <button
                key={s}
                type="button"
                aria-pressed={sport === s}
                className={`tone-${style.tone}${sport === s ? " is-active" : ""}`}
                onClick={() => setChosenSport(s)}
              >
                <Icon name={style.icon} size={16} />
                {INSIGHTS_SPORTS[s].label}
              </button>
            );
          })}
        </div>
        {commuteFilter && (
          <label className="insights-commutes">
            <input
              type="checkbox"
              checked={excludeCommutesChoice}
              onChange={(e) => chooseExcludeCommutes(e.target.checked)}
            />
            Exclude commutes
          </label>
        )}
      </div>
      <div className="insights-tabs" role="tablist">
        {TABS.filter((t) => tabAvailable(t, sport)).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            className={tab === t ? "is-active" : undefined}
            onClick={() => setTab(t)}
          >
            {tabLabel(t, sport)}
          </button>
        ))}
      </div>

      {tab === "pace-trends" &&
        activityList((data) =>
          sport === "cycling" ? (
            <PowerTrendsChart activities={data} excludeCommutes={excludeCommutes} />
          ) : (
            <PaceTrendsChart activities={data} />
          ),
        )}
      {tab === "pr-progress" &&
        activityList((data) => <PrProgressChart activities={data} sport={sport} />)}
      {tab === "training-bands" && (
        <TrainingBandsChart sport={sport} excludeCommutes={excludeCommutes} />
      )}
      {tab === "race-predictions" && <RacePredictionsChart />}
      {tab === "race-readiness" && <RaceReadinessChart />}
      {tab === "pace-hr-zones" && (
        <PaceHrZonesTable sport={sport} excludeCommutes={excludeCommutes} />
      )}
      {tab === "vo2max" &&
        (sport === "cycling" ? (
          <CyclingPowerPanel excludeCommutes={excludeCommutes} />
        ) : (
          <>
            <Vo2maxChart />
            <Vo2maxFactorAnalysis />
          </>
        ))}
      {tab === "performance-curve" && (
        <PerformanceCurveChart sport={sport} excludeCommutes={excludeCommutes} />
      )}
      {tab === "eddington" && <EddingtonChart sport={sport} excludeCommutes={excludeCommutes} />}
    </main>
  );
}
