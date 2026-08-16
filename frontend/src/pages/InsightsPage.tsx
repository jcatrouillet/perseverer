// Phase 8 Milestone D (see docs/adr/0012-phase-8-strava-merge-insights.md): a plain read of
// GET /insights, grouped by kind and filtered to one window at a time -- the real archive
// produces ~250+ effort rows alone across all five windows, so showing every window at once
// would bury the page; "current"-window rows (the running streak, load/health flags) always
// show regardless of the selected window, since they describe "right now", not a lookback.
import { useState } from "react";

import { useInsights } from "../api/queries";
import { InsightCard } from "../components/InsightCard";
import {
  CURRENT_WINDOW,
  KIND_LABELS,
  KIND_ORDER,
  WINDOW_LABELS,
  WINDOWS,
  groupByKind,
  visibleInsights,
} from "../insightsView";
import "../styles/insights.css";

export function InsightsPage() {
  const [window, setWindow] = useState<string>("30d");
  const insights = useInsights();

  const filtered = insights.data ? visibleInsights(insights.data, window) : [];
  const groups = groupByKind(filtered);

  return (
    <main>
      <h1>Insights</h1>
      <label className="insights-window-select">
        Window
        <select value={window} onChange={(e) => setWindow(e.target.value)}>
          {WINDOWS.map((w) => (
            <option key={w} value={w}>
              {WINDOW_LABELS[w]}
            </option>
          ))}
        </select>
      </label>

      {insights.isLoading && <p>Loading…</p>}
      {insights.isError && <p role="alert">Could not load insights.</p>}

      {insights.data && filtered.length === 0 && (
        <p>Nothing to report for this window yet -- check back after a few more activities.</p>
      )}

      {KIND_ORDER.map((kind) => {
        const items = groups.get(kind) ?? [];
        if (items.length === 0) return null;
        return (
          <section className="card" key={kind}>
            <h2>{KIND_LABELS[kind]}</h2>
            <ul className="insight-list">
              {items.map((insight, i) => (
                <li key={`${insight.window === CURRENT_WINDOW ? "cur" : insight.window}-${i}`}>
                  <InsightCard insight={insight} />
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </main>
  );
}
