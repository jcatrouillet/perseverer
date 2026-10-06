// The interactive trim UI for a hiking/walking recording flagged as likely including a stretch
// of car travel (see transport_mix.py's own detection heuristic). Entirely client-side for the
// live preview -- ActivityDetailPage already fetches this activity's own high-tier stream for
// the normal Route section, and buildRouteData's {points, distanceM, elapsedS} is exactly what's
// needed to slice a preview: no new backend endpoint, no round-trip per slider drag. The actual
// commit (POST/DELETE .../trim) is a real backend recompute from the full-resolution Parquet
// stream -- this preview is "roughly what you're keeping," not a promise of exact post-commit
// numbers (see activity_trim.py's own docstring for what gets recomputed vs. cleared).
//
// Purely presentational otherwise, like BoulderingRoutesTable -- the parent (ActivityDetailPage)
// owns the actual trim mutation and passes bound onCommit/onCancel callbacks down; this
// component only owns its own local slider-position state.
import { useState } from "react";

import type { LapOut } from "../api/types";
import type { RouteData } from "./ActivityRoute";
import { ActivityRouteMap } from "./ActivityRouteMap";
import { formatClockDuration } from "../runningStats";

function lapElapsedRange(
  lap: LapOut,
  activityStartTimeUtc: string,
): { startS: number; endS: number } {
  const startS =
    (new Date(lap.start_time_utc).getTime() - new Date(activityStartTimeUtc).getTime()) / 1000;
  const durationS = lap.moving_duration_s ?? lap.duration_s ?? 0;
  return { startS, endS: startS + durationS };
}

export function ActivityTrimControls({
  route,
  laps,
  activityStartTimeUtc,
  suggestedTrimStartS,
  suggestedTrimEndS,
  onCommit,
  onCancel,
  isSaving,
  isError,
}: {
  route: RouteData;
  laps: LapOut[];
  activityStartTimeUtc: string;
  suggestedTrimStartS: number | null;
  suggestedTrimEndS: number | null;
  onCommit: (trimStartS: number | null, trimEndS: number | null) => void;
  onCancel: () => void;
  isSaving: boolean;
  isError: boolean;
}) {
  const totalS = route.elapsedS[route.elapsedS.length - 1] ?? 0;
  const [trimStartS, setTrimStartS] = useState(
    Math.max(0, Math.min(suggestedTrimStartS ?? 0, totalS)),
  );
  const [trimEndS, setTrimEndS] = useState(
    Math.max(0, Math.min(suggestedTrimEndS ?? totalS, totalS)),
  );

  const startIdx = route.elapsedS.findIndex((t) => t >= trimStartS);
  let endIdx = -1;
  for (let i = route.elapsedS.length - 1; i >= 0; i--) {
    if (route.elapsedS[i]! <= trimEndS) {
      endIdx = i;
      break;
    }
  }
  const valid = startIdx >= 0 && endIdx >= startIdx;
  // route.elapsedS (used for the slider domain above) is pause-compressed for a cleaner
  // map/slider experience (see ActivityRoute.tsx's own docstring) -- but the backend's trim
  // endpoint expects raw, uncompressed elapsed seconds. Indices are shared 1:1 between the two
  // parallel arrays, so look up the *raw* boundary at the same index rather than sending the
  // compressed trimStartS/trimEndS state directly (which, for any activity with a real device
  // pause, would silently commit a much shorter/earlier window than the slider promised).
  const rawTrimStartS = valid ? (route.rawElapsedS[startIdx] ?? null) : null;
  const rawTrimEndS = valid ? (route.rawElapsedS[endIdx] ?? null) : null;

  const previewPoints = valid ? route.points.slice(startIdx, endIdx + 1) : [];
  const previewDistanceM = valid ? route.distanceM.slice(startIdx, endIdx + 1) : [];
  const previewElapsedS = valid ? route.elapsedS.slice(startIdx, endIdx + 1) : [];

  const firstDistance = previewDistanceM.find((d) => d != null) ?? null;
  const lastDistance = [...previewDistanceM].reverse().find((d) => d != null) ?? null;
  const keptDistanceM =
    firstDistance != null && lastDistance != null ? lastDistance - firstDistance : null;
  const keptDurationS = valid ? trimEndS - trimStartS : 0;

  const lapRows = laps.map((lap) => {
    const range = lapElapsedRange(lap, activityStartTimeUtc);
    const kept = range.endS > trimStartS && range.startS < trimEndS;
    return { lap, kept };
  });

  const handleStartChange = (value: number) => {
    setTrimStartS(Math.min(value, trimEndS - 1));
  };
  const handleEndChange = (value: number) => {
    setTrimEndS(Math.max(value, trimStartS + 1));
  };

  return (
    <section className="card activity-trim">
      <h2>Trim this activity</h2>
      <p className="activity-trim__hint">
        Drag either handle to preview what you'd keep, then save. Distance, duration, elevation
        gain, and heart rate are recomputed for the kept window; calories and training load aren't
        re-derivable from just part of a recording, so they'll show as unavailable after trimming.
      </p>

      <div className="activity-trim__map">
        {valid && previewPoints.length >= 2 ? (
          <ActivityRouteMap
            points={previewPoints}
            distanceM={previewDistanceM}
            elapsedS={previewElapsedS}
            highlightRange={null}
            markerIndex={null}
          />
        ) : (
          <p role="alert">Not enough of the recording is left to preview.</p>
        )}
      </div>

      <div className="activity-trim__sliders">
        <label className="activity-trim__slider-row">
          <span>Trim from start</span>
          <input
            type="range"
            min={0}
            max={Math.round(totalS)}
            value={Math.round(trimStartS)}
            onChange={(e) => handleStartChange(Number(e.target.value))}
          />
          <span className="activity-trim__slider-readout">{formatClockDuration(trimStartS)}</span>
        </label>
        <label className="activity-trim__slider-row">
          <span>Keep until</span>
          <input
            type="range"
            min={0}
            max={Math.round(totalS)}
            value={Math.round(trimEndS)}
            onChange={(e) => handleEndChange(Number(e.target.value))}
          />
          <span className="activity-trim__slider-readout">{formatClockDuration(trimEndS)}</span>
        </label>
      </div>

      <div className="activity-trim__summary">
        <span>Kept duration: {formatClockDuration(keptDurationS)}</span>
        {keptDistanceM != null && (
          <span>Kept distance: {(keptDistanceM / 1000).toFixed(2)} km</span>
        )}
      </div>

      {laps.length > 0 && (
        <div className="activity-trim__laps">
          <h3>Intervals</h3>
          <ul>
            {lapRows.map(({ lap, kept }, i) => (
              <li key={lap.lap_index} className={kept ? undefined : "activity-trim__lap--dropped"}>
                Lap {i + 1} {kept ? "" : "(dropped)"}
              </li>
            ))}
          </ul>
        </div>
      )}

      {isError && (
        <p role="alert" className="activity-trim__error">
          Could not save this trim. Try again.
        </p>
      )}

      <div className="activity-trim__actions">
        <button
          type="button"
          className="button button--primary"
          disabled={!valid || isSaving}
          onClick={() =>
            onCommit(trimStartS > 0 ? rawTrimStartS : null, trimEndS < totalS ? rawTrimEndS : null)
          }
        >
          {isSaving ? "Saving…" : "Save trim"}
        </button>
        <button type="button" className="button" onClick={onCancel} disabled={isSaving}>
          Cancel
        </button>
      </div>
    </section>
  );
}
