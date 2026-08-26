// Phase 7 Milestone A (docs/adr/0011-phase-7-map-recaps-pwa.md): every GPS-bearing activity's
// start point on one map. Leaflet + public OpenStreetMap tiles, not MapLibre + self-hosted
// Protomaps -- a deliberate, user-confirmed scope reduction from the project brief (ADR 0011
// decision 1), traded for far less setup at the cost of map-viewport tile requests going to a
// third party (never raw GPS data, which stays server-side).
//
// One bounded fetch (no filters sent to the API -- real scale is 904 points, confirmed against
// the archive, so there's nothing to paginate), then sport/date filtering happens entirely
// client-side against that one already-loaded set: instant filtering, and the sport dropdown's
// own options come from the real data rather than a hardcoded catalog that could go stale.
import "leaflet/dist/leaflet.css";
import type { LatLngBoundsExpression } from "leaflet";
import { useMemo, useState } from "react";
import { CircleMarker, MapContainer, Popup, TileLayer } from "react-leaflet";
import { Link } from "wouter";

import { useActivityMapPoints } from "../api/queries";
import type { ActivityMapPointOut } from "../api/types";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { EARLIEST_PLAUSIBLE_DATE } from "../dateUtils";
import { sportStyle, toneColor } from "../metricStyle";
import "../styles/map-explorer.css";

// Wide enough to comfortably contain any single point (Leaflet errors on a zero-area bounds).
const WORLD_BOUNDS: LatLngBoundsExpression = [
  [-60, -170],
  [70, 170],
];

function formatDistance(distanceM: number | null): string | null {
  if (distanceM == null || distanceM <= 0) return null;
  return `${(distanceM / 1000).toFixed(1)} km`;
}

export function MapExplorerPage() {
  const points = useActivityMapPoints({});
  const [sport, setSport] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  // The same real-device-clock-glitch filter every other all-history view already applies
  // (dateUtils.ts::EARLIEST_PLAUSIBLE_DATE) -- a handful of activities carry pre-GPS dates like
  // 1989-12-30 from a known GPS week-number rollover bug, not real history.
  const allPoints = (points.data ?? []).filter(
    (p) => p.local_date == null || p.local_date >= EARLIEST_PLAUSIBLE_DATE,
  );

  const sportOptions = useMemo(
    () => Array.from(new Set(allPoints.map((p) => p.sport))).sort(),
    [allPoints],
  );

  const filtered = allPoints.filter((p) => {
    if (sport && p.sport !== sport) return false;
    if (startDate && (p.local_date == null || p.local_date < startDate)) return false;
    if (endDate && (p.local_date == null || p.local_date > endDate)) return false;
    return true;
  });

  const bounds: LatLngBoundsExpression =
    filtered.length > 0 ? filtered.map((p) => [p.start_lat, p.start_lng]) : WORLD_BOUNDS;

  return (
    <main>
      <h1>Map explorer</h1>
      <p className="map-explorer__count">
        {points.isLoading && <LoadingSpinner size="sm" />}
        {points.isError && <span role="alert">Could not load activity locations.</span>}
        {points.data && `${filtered.length} of ${allPoints.length} activities shown`}
      </p>

      <form className="map-explorer__filters">
        <label className="field">
          Sport
          <select className="input" value={sport} onChange={(e) => setSport(e.target.value)}>
            <option value="">All sports</option>
            {sportOptions.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          From
          <input
            className="input"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </label>
        <label className="field">
          To
          <input
            className="input"
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </label>
      </form>

      <div className="map-explorer__map">
        <MapContainer bounds={bounds} boundsOptions={{ padding: [24, 24] }} scrollWheelZoom>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          {filtered.map((p) => (
            <MapPoint key={p.id} point={p} />
          ))}
        </MapContainer>
      </div>
    </main>
  );
}

function MapPoint({ point }: { point: ActivityMapPointOut }) {
  const style = sportStyle(point.sport);
  const color = toneColor(style.tone);
  const distance = formatDistance(point.distance_m);
  return (
    <CircleMarker
      center={[point.start_lat, point.start_lng]}
      radius={5}
      pathOptions={{ color, fillColor: color, fillOpacity: 0.75, weight: 1 }}
    >
      <Popup>
        <div className="map-explorer__popup">
          <strong>{point.sport.replace(/_/g, " ")}</strong>
          {point.name && <span>{point.name}</span>}
          <span>
            {point.local_date}
            {distance && ` · ${distance}`}
          </span>
          <Link href={`/activities/${point.id}`}>View activity →</Link>
        </div>
      </Popup>
    </CircleMarker>
  );
}
