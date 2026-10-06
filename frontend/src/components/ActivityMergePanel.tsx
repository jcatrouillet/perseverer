// The manual "these two activities are the same, use this side's data for each field" tool --
// the logical inverse of ActivitySourcesPanel's split feature, and lives in the same Sources
// area of the page for that reason. Purely presentational, like ActivitySourcesPanel/
// BoulderingRoutesTable/ActivityTrimControls -- the page owns the actual data fetching (the
// merge preview needs a dynamic `otherId` depending on which candidate is being reviewed, which
// this component can't own itself without breaking the "no internal hooks" convention every
// other editing surface in this app follows) and the merge mutation, passing bound callbacks +
// state down.
import { useState } from "react";
import { Link } from "wouter";

import type { ActivityMergePreviewOut, DuplicateCandidateOut } from "../api/types";
import { formatDurationHM } from "../runningStats";

const SOURCE_LABELS: Record<string, string> = {
  fit_folder: "FIT file",
  garmin_export: "Garmin export",
  garmin_connect: "Garmin Connect",
  strava_export: "Strava export",
};

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

const FIELD_LABELS: Record<string, string> = {
  distance_m: "Distance",
  duration_s: "Duration",
  moving_duration_s: "Moving time",
  elevation_gain_m: "Elevation gain",
  max_altitude_m: "Max elevation",
  calories: "Calories",
  avg_hr_bpm: "Avg heart rate",
  max_hr_bpm: "Max heart rate",
  training_load: "Training load",
  route: "Route",
  laps: "Laps",
  splits: "Splits",
  stream: "Charts data",
};

const COLLECTION_FIELDS = new Set(["route", "laps", "splits", "stream"]);

function formatFieldValue(field: string, value: number | string | null): string {
  if (COLLECTION_FIELDS.has(field)) return "(whichever side is chosen)";
  if (value == null) return "—";
  if (typeof value === "string") return value;
  switch (field) {
    case "distance_m":
      return `${(value / 1000).toFixed(2)} km`;
    case "duration_s":
    case "moving_duration_s":
      return formatDurationHM(value);
    case "elevation_gain_m":
    case "max_altitude_m":
      return `${value.toFixed(0)} m`;
    case "calories":
      return `${value.toFixed(0)} kcal`;
    case "avg_hr_bpm":
    case "max_hr_bpm":
      return `${Math.round(value)} bpm`;
    default:
      return String(value);
  }
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function ActivityMergePanel({
  candidates,
  expandedCandidateId,
  onExpandCandidate,
  preview,
  isPreviewLoading,
  onMerge,
  isMerging,
  mergeError,
}: {
  candidates: DuplicateCandidateOut[];
  expandedCandidateId: string | null;
  onExpandCandidate: (id: string | null) => void;
  preview: ActivityMergePreviewOut | undefined;
  isPreviewLoading: boolean;
  onMerge: (otherActivityId: string, fieldChoices: Record<string, string>) => void;
  isMerging: boolean;
  mergeError: boolean;
}) {
  // Per-field choice for the currently-expanded candidate only -- "self" (this activity's own
  // value, the default) or "other" (the candidate's). Reset whenever a different candidate is
  // opened for review.
  const [choices, setChoices] = useState<Record<string, "self" | "other">>({});

  if (candidates.length === 0) return null;

  const startReview = (id: string) => {
    setChoices({});
    onExpandCandidate(id);
  };

  return (
    <section className="card activity-merge">
      <h2>Possible duplicate</h2>
      {candidates.map((candidate) => (
        <div key={candidate.id} className="activity-merge__candidate">
          <p className="activity-merge__candidate-text">
            This might be the same as{" "}
            <Link href={`/activities/${candidate.id}`}>
              {candidate.name ?? "this activity"} ({sourceLabel(candidate.primary_source)},{" "}
              {formatDate(candidate.start_time_utc)})
            </Link>
            .
          </p>
          {expandedCandidateId !== candidate.id && (
            <button type="button" className="button" onClick={() => startReview(candidate.id)}>
              Review merge
            </button>
          )}

          {expandedCandidateId === candidate.id && (
            <div className="activity-merge__review">
              {isPreviewLoading && <p>Loading comparison…</p>}
              {preview && (
                <>
                  <table className="activity-merge__table">
                    <thead>
                      <tr>
                        <th>Field</th>
                        <th>This activity</th>
                        <th>{sourceLabel(candidate.primary_source)}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.fields.map((f) => (
                        <tr key={f.field}>
                          <td>{FIELD_LABELS[f.field] ?? f.field}</td>
                          <td>
                            <label>
                              <input
                                type="radio"
                                name={`merge-${f.field}`}
                                checked={(choices[f.field] ?? "self") === "self"}
                                onChange={() => setChoices((c) => ({ ...c, [f.field]: "self" }))}
                              />
                              {formatFieldValue(f.field, f.self_value)}
                            </label>
                          </td>
                          <td>
                            <label>
                              <input
                                type="radio"
                                name={`merge-${f.field}`}
                                checked={choices[f.field] === "other"}
                                onChange={() => setChoices((c) => ({ ...c, [f.field]: "other" }))}
                              />
                              {formatFieldValue(f.field, f.other_value)}
                            </label>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {mergeError && (
                    <p role="alert" className="activity-merge__error">
                      Could not merge these activities. Try again.
                    </p>
                  )}

                  <div className="activity-merge__actions">
                    <button
                      type="button"
                      className="button button--primary"
                      disabled={isMerging}
                      onClick={() => onMerge(candidate.id, choices)}
                    >
                      {isMerging ? "Merging…" : "Confirm merge"}
                    </button>
                    <button
                      type="button"
                      className="button"
                      disabled={isMerging}
                      onClick={() => onExpandCandidate(null)}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
