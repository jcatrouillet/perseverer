// A small, non-interactive route thumbnail for the activity list and day view -- the user's own
// framing ("a map of the activities... must be shown... central to this app"). Reuses the same
// Leaflet + public OSM tile stack MapExplorerPage already verified (ADR 0011 decision 1/2), just
// with all interaction disabled and zoomed to fit the one route via `decodePolyline()`.
//
// CARTO's Positron basemap (a much lower-detail tile set -- no building outlines/POI icons/
// road-name clutter -- built for exactly this "route thumbnail" use) and the same 220px height
// every other route map in the app uses (activity-map.css), everywhere a route thumbnail
// appears -- day view's own cards fall back to this exact component/size for an activity with no
// stream to animate yet, so the two need to actually match, not just both be called "the day
// view map."
//
// Deliberately still CARTO's *raster* tiles, not the vector basemap ActivityRouteMap.tsx/
// MapExplorerPage.tsx moved to (CartoBasemapLayer.tsx) -- confirmed live (not theoretical) that
// this component doesn't get that upgrade: an activity list page renders one of these thumbnails
// per activity, and each vector basemap spins up its own MapLibre GL WebGL canvas. Chrome caps
// live WebGL contexts at 16 per page; with more thumbnails than that on screen (completely
// ordinary here -- a real activity list page had 31), the browser silently evicts the oldest
// contexts to make room for new ones, and an evicted canvas never recovers -- no error anywhere,
// it just renders nothing forever. Reproduced directly: the first 15 of 31 thumbnails came back
// `contextLost: true` while the rest didn't. Raster tiles are plain `<img>` elements with no such
// per-page ceiling, so this is the one map surface in the app that stays on them for real
// architectural reasons, not just historical inertia -- see CartoBasemapLayer.tsx's own
// docstring and AGENTS.md's Frontend bullet for the fuller picture across all three map surfaces.
import "leaflet/dist/leaflet.css";
import type { LatLngBoundsExpression } from "leaflet";
import { MapContainer, Polyline, TileLayer } from "react-leaflet";

import { cartoRasterTileUrlTemplate } from "../mapBasemap";
import { decodePolyline } from "../polyline";
import "../styles/activity-map.css";

export function ActivityMap({
  encodedPolyline,
  color,
}: {
  encodedPolyline: string;
  /** The route's stroke colour -- callers pass `toneColor(sportStyle(activity.sport).tone)` so
   * a run's thumbnail route reads in the same hue as its sport icon everywhere else in the app,
   * rather than this component inventing its own decorative colour (ADR 0010's "a hue always
   * identifies a metric" rule). */
  color: string;
}) {
  const points = decodePolyline(encodedPolyline);
  if (points.length < 2) return null;
  const bounds: LatLngBoundsExpression = points;

  return (
    <div className="activity-map">
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
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
          url={cartoRasterTileUrlTemplate()}
        />
        <Polyline positions={points} pathOptions={{ color, weight: 3, opacity: 0.9 }} />
      </MapContainer>
    </div>
  );
}
