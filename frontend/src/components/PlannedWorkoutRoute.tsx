// The GPX route on a planned running workout: a small map with the route's name, distance and
// climb, plus Attach/Replace/Remove. Purely presentational (like BoulderingRoutesTable) -- the
// caller owns the mutations. The route is for the athlete's own reference on the calendar; it is
// not sent to the watch (see docs/adr/0017-planned-workout-gpx-route.md).
import { useRef } from "react";

import type { PlannedRouteOut } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import { toneColor } from "../metricStyle";
import { ActivityMap } from "./ActivityMap";

export function PlannedWorkoutRoute({
  route,
  onAttach,
  onRemove,
  isBusy,
  error,
}: {
  route: PlannedRouteOut | null;
  onAttach: (file: File) => void;
  onRemove: () => void;
  isBusy: boolean;
  error: string | null;
}) {
  const { formatDistance } = useDistanceFormat();
  const inputRef = useRef<HTMLInputElement>(null);

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) onAttach(file);
    // Clearing lets the same file be chosen again after a failed attempt.
    e.target.value = "";
  }

  return (
    <div className="planned-workout__route">
      {route && (
        <>
          <p className="chart-note planned-workout__route-caption">
            <strong>{route.name ?? "GPX route"}</strong>
            {" · "}
            {formatDistance(route.distance_m)}
            {route.elevation_gain_m != null && ` · +${Math.round(route.elevation_gain_m)} m`}
          </p>
          <ActivityMap encodedPolyline={route.polyline} color={toneColor("pace")} />
        </>
      )}
      <div className="planned-workout__actions">
        <input
          ref={inputRef}
          type="file"
          accept=".gpx,application/gpx+xml,application/xml,text/xml"
          hidden
          aria-label="GPX file"
          onChange={handleFile}
        />
        <button
          type="button"
          className="button"
          disabled={isBusy}
          onClick={() => inputRef.current?.click()}
        >
          {isBusy ? "Uploading…" : route ? "Replace GPX" : "Attach GPX route"}
        </button>
        {route && (
          <button type="button" className="button" disabled={isBusy} onClick={onRemove}>
            Remove route
          </button>
        )}
      </div>
      {error && (
        <p className="chart-note" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
