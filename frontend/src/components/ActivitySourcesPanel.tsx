// Phase 8 Milestone B (see docs/adr/0012-phase-8-strava-merge-insights.md): shows every source
// an activity's data actually came from, plus why they were merged together, and lets a human
// undo a wrong merge -- the "both sources inspectable" acceptance criterion made visible.
// Presentational only (data-fetching hooks live in ActivityDetailPage), matching this codebase's
// existing split between page-level fetching and prop-driven components (ActivityWeather,
// ActivityContextStrip).
import { useState } from "react";

import type { ActivitySourcesOut } from "../api/types";
import { MetricChip } from "./StatTile";

const SOURCE_LABELS: Record<string, string> = {
  fit_folder: "FIT file",
  garmin_export: "Garmin export",
  garmin_connect: "Garmin Connect",
  strava_export: "Strava export",
};

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

function formatIngestedAt(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function ActivitySourcesPanel({
  sources,
  onSplit,
  isSplitting,
  splitError,
  splitSuccess,
}: {
  sources: ActivitySourcesOut;
  onSplit: (linkId: number) => void;
  isSplitting: boolean;
  splitError: boolean;
  splitSuccess: boolean;
}) {
  const [confirmingLinkId, setConfirmingLinkId] = useState<number | null>(null);

  if (sources.sources.length === 0) return null;
  const { sources: links, merge_decisions: mergeDecisions } = sources;

  return (
    <section className="card activity-sources">
      <h2>Sources</h2>
      <ul className="activity-sources__list">
        {links.map((link) => (
          <li key={link.link_id} className="activity-sources__row">
            <MetricChip label={sourceLabel(link.source)} />
            <span className="activity-sources__meta">
              linked {formatIngestedAt(link.ingested_at)}
            </span>
            {link.can_split && confirmingLinkId !== link.link_id && (
              <button
                type="button"
                className="activity-sources__split-btn"
                onClick={() => setConfirmingLinkId(link.link_id)}
              >
                Not the same activity?
              </button>
            )}
            {confirmingLinkId === link.link_id && (
              <span className="activity-sources__confirm">
                Split this source into its own activity?
                <button
                  type="button"
                  disabled={isSplitting}
                  onClick={() => {
                    onSplit(link.link_id);
                    setConfirmingLinkId(null);
                  }}
                >
                  {isSplitting ? "Splitting…" : "Confirm"}
                </button>
                <button type="button" onClick={() => setConfirmingLinkId(null)}>
                  Cancel
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {splitError && <p role="alert">Could not split this source. Please try again.</p>}
      {splitSuccess && <p>Split into a new activity.</p>}

      {mergeDecisions.length > 0 && (
        <details className="activity-sources__decisions">
          <summary>Why these were merged</summary>
          <ul>
            {mergeDecisions.map((decision, i) => (
              <li key={i}>{decision.reasons.join("; ")}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
