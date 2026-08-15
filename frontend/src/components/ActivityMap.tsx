// A small, non-interactive route thumbnail for the activity list and day view -- the user's own
// framing ("a map of the activities... must be shown... central to this app"). Reuses the same
// Leaflet + public OSM tile stack MapExplorerPage already verified (ADR 0011 decision 1/2), just
// with all interaction disabled and zoomed to fit the one route via `decodePolyline()`.
//
// The detailed, pace-coloured, per-km-hoverable map on the activity detail page is a separate
// component (ActivityRouteMap.tsx) built from the full-resolution stream, not this one -- a
// thumbnail's whole job is "recognizable at a glance", a detail map's is "precise enough to
// analyse", and trying to make one component do both would compromise both.
import "leaflet/dist/leaflet.css";
import type { LatLngBoundsExpression } from "leaflet";
import { MapContainer, Polyline, TileLayer } from "react-leaflet";

import { decodePolyline } from "../polyline";
import "../styles/activity-map.css";

export function ActivityMap({
  encodedPolyline,
  color,
  size = "thumbnail",
}: {
  encodedPolyline: string;
  /** The route's stroke colour -- callers pass `toneColor(sportStyle(activity.sport).tone)` so
   * a run's thumbnail route reads in the same hue as its sport icon everywhere else in the app,
   * rather than this component inventing its own decorative colour (ADR 0010's "a hue always
   * identifies a metric" rule). */
  color: string;
  /** "thumbnail" (default, the activity list's dense card grid) keeps the original wide/short
   * proportions and standard OSM tiles. "large" (the day view, one card per activity) is taller
   * and narrower, and switches to CARTO's Positron basemap -- a much lower-detail (no building
   * outlines/POI icons/road-name clutter) tile set built for exactly this "route thumbnail"
   * use, confirmed reachable with a direct tile fetch before wiring it in. */
  size?: "thumbnail" | "large";
}) {
  const points = decodePolyline(encodedPolyline);
  if (points.length < 2) return null;
  const bounds: LatLngBoundsExpression = points;
  const large = size === "large";

  return (
    <div className={`activity-map ${large ? "activity-map--large" : "activity-map--thumbnail"}`}>
      <MapContainer
        bounds={bounds}
        boundsOptions={{ padding: [6, 6] }}
        zoomControl={false}
        dragging={false}
        scrollWheelZoom={false}
        doubleClickZoom={false}
        touchZoom={false}
        boxZoom={false}
        keyboard={false}
      >
        {large ? (
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
            url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
          />
        ) : (
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
        )}
        <Polyline positions={points} pathOptions={{ color, weight: 3, opacity: 0.9 }} />
      </MapContainer>
    </div>
  );
}
