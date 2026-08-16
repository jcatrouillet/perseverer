// The detailed, pace-coloured route map for the activity detail page (distinct from
// ActivityMap.tsx's non-interactive list/day-view thumbnail -- see that file's own docstring for
// why they're separate components). Colours each segment of the route by how fast it was run
// relative to the rest of the activity (blue = faster, red = slower, matching the reference
// image's legend), supports highlighting one split's segment when its row is hovered in
// SplitsTable, and can show a single moving marker for the playback animation in ActivityRoute.
import "leaflet/dist/leaflet.css";
import { DivIcon, type LatLngBoundsExpression, type LatLngTuple } from "leaflet";
import { useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Marker, Polyline, TileLayer, useMap } from "react-leaflet";

import { hexToRgb, lerpColor, type Rgb } from "../colorGradient";
import "../styles/activity-route.css";

export interface RoutePoint {
  lat: number;
  lon: number;
}

export interface Segment {
  positions: LatLngTuple[];
  color: string;
  /** Indices into the `points` array this segment spans -- lets a consumer (routeExport.ts's
   * PNG poster, which projects points to its own canvas coordinates rather than lat/lon) re-
   * slice its own projected points the same way, instead of re-deriving `positions` back into
   * indices or duplicating the chunking math. */
  start: number;
  end: number;
}

// Capping the rendered segment count keeps this smooth even on a multi-hour activity's ~20,000-
// point high-tier stream -- a gradient this fine-grained reads as continuous at map scale anyway.
const TARGET_SEGMENTS = 250;
// Below this, a GPS auto-pause/stopped-at-a-light chunk reads as "infinitely slow" and would pin
// the whole colour scale to its red end -- matches streamSpeedValue's own stationary floor.
const MIN_MOVING_SPEED_MPS = 0.3;

// A genuinely checkered finish marker (plain-fill CircleMarkers can't render a pattern) --
// a small inline black/white checker SVG as a Leaflet DivIcon, built once at module scope
// since it doesn't depend on any prop.
const FINISH_ICON = new DivIcon({
  className: "activity-route-map__finish-icon",
  // Four alternating black/white quadrants via plain arc paths -- no <clipPath> id needed
  // (this HTML string is reused verbatim for every ActivityRouteMap instance on a page, e.g.
  // one per activity in the Milestone D day view, and SVG ids aren't guaranteed unique across
  // duplicated inline markup). Reads as a checker flag at marker scale; a finer 8-square grid
  // wouldn't be distinguishable at 18px anyway.
  html: `<svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
    <circle cx="9" cy="9" r="8" fill="#fff" stroke="var(--color-surface)" stroke-width="2"/>
    <path d="M9,9 L9,1 A8,8 0 0,1 17,9 Z" fill="#111"/>
    <path d="M9,9 L9,17 A8,8 0 0,1 1,9 Z" fill="#111"/>
  </svg>`,
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

export function resolveTone(cssVar: string): Rgb {
  const value = getComputedStyle(document.documentElement).getPropertyValue(cssVar).trim();
  return hexToRgb(value);
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor(p * (sorted.length - 1))));
  return sorted[idx]!;
}

export function buildSegments(
  points: RoutePoint[],
  distanceM: (number | null)[],
  elapsedS: number[],
  slowRgb: Rgb,
  fastRgb: Rgb,
): Segment[] {
  const legCount = points.length - 1;
  if (legCount <= 0) return [];
  const chunkSize = Math.max(1, Math.ceil(legCount / TARGET_SEGMENTS));

  const chunks: { start: number; end: number; speedMps: number | null }[] = [];
  for (let start = 0; start < legCount; start += chunkSize) {
    const end = Math.min(start + chunkSize, points.length - 1);
    const d0 = distanceM[start];
    const d1 = distanceM[end];
    const dt = elapsedS[end]! - elapsedS[start]!;
    const speedMps = d0 != null && d1 != null && dt > 0 ? (d1 - d0) / dt : null;
    chunks.push({ start, end, speedMps });
  }

  const movingSpeeds = chunks
    .map((c) => c.speedMps)
    .filter((s): s is number => s != null && s >= MIN_MOVING_SPEED_MPS)
    .sort((a, b) => a - b);
  const slow = percentile(movingSpeeds, 0.05);
  const fast = percentile(movingSpeeds, 0.95);
  const range = fast - slow;

  return chunks.map((c) => {
    const t =
      c.speedMps == null || c.speedMps < MIN_MOVING_SPEED_MPS || range <= 0
        ? 0
        : (c.speedMps - slow) / range;
    return {
      positions: points.slice(c.start, c.end + 1).map((p): LatLngTuple => [p.lat, p.lon]),
      color: lerpColor(slowRgb, fastRgb, t),
      start: c.start,
      end: c.end,
    };
  });
}

/** Leaflet caches its container's pixel size at creation time and never re-measures on its own,
 * so a map that starts at 360px tall and then gets stretched to fill the screen (the fullscreen
 * toggle in ActivityRoute.tsx) renders into a stale, wrong-sized canvas until told to
 * re-measure. `trigger` is any value that changes exactly when the container's size changes
 * (e.g. a fullscreen boolean) -- the timeout lets the CSS layout that actually resizes the
 * container finish before Leaflet re-measures it, not the other way around. */
function MapResizeHandler({ trigger }: { trigger: unknown }) {
  const map = useMap();
  useEffect(() => {
    const id = setTimeout(() => map.invalidateSize(), 50);
    return () => clearTimeout(id);
  }, [map, trigger]);
  return null;
}

export function ActivityRouteMap({
  points,
  distanceM,
  elapsedS,
  highlightRange,
  markerIndex,
  resizeSignal,
}: {
  points: RoutePoint[];
  /** Cumulative distance, parallel to `points`. */
  distanceM: (number | null)[];
  /** Seconds elapsed since activity start, parallel to `points`. */
  elapsedS: number[];
  /** A split's [startIndex, endIndex] into `points` to highlight, or null for none. */
  highlightRange: [number, number] | null;
  /** Index into `points` for the playback animation's current-position marker, or null. */
  markerIndex: number | null;
  /** Changes exactly when this map's container has been resized by its caller (e.g. entering/
   * exiting fullscreen) -- see MapResizeHandler above. Omitted by callers whose container size
   * never changes after mount. */
  resizeSignal?: unknown;
}) {
  const [tones, setTones] = useState<{ slow: Rgb; fast: Rgb } | null>(null);

  useEffect(() => {
    // Fast = red (--color-heart-rate), slow = blue (--color-pace) -- confirmed against the
    // theme's real hex values (ADR 0013); the reverse of the original binding.
    setTones({ slow: resolveTone("--color-pace"), fast: resolveTone("--color-heart-rate") });
  }, []);

  const segments = useMemo(
    () =>
      tones ? buildSegments(points, distanceM, elapsedS, tones.slow, tones.fast) : [],
    [points, distanceM, elapsedS, tones],
  );

  if (points.length < 2) return null;

  const bounds: LatLngBoundsExpression = points.map((p): LatLngTuple => [p.lat, p.lon]);
  const start = points[0]!;
  const finish = points[points.length - 1]!;
  const marker = markerIndex != null ? points[markerIndex] : null;
  const highlightPositions = highlightRange
    ? points.slice(highlightRange[0], highlightRange[1] + 1).map((p): LatLngTuple => [p.lat, p.lon])
    : null;

  return (
    <div className="activity-route-map">
      <MapContainer bounds={bounds} boundsOptions={{ padding: [16, 16] }} scrollWheelZoom>
        <MapResizeHandler trigger={resizeSignal} />
        {/* CARTO Positron -- the same lower-detail basemap ActivityMap.tsx's day-view "large"
            thumbnail already uses (verified reachable in Phase 7); a route this detailed reads
            better without OSM's building outlines/POI icons/road-name clutter underneath it. */}
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
          url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
        />
        {segments.map((seg, i) => (
          <Polyline key={i} positions={seg.positions} pathOptions={{ color: seg.color, weight: 4, opacity: 0.9 }} />
        ))}
        {highlightPositions && (
          <>
            {/* A pale casing under a fully-opaque core, the same contrast trick the start/finish/
                marker circles below already use (a `--color-surface` ring) -- a single
                semi-transparent line in the same hue family as the gradient it sits on top of
                was confirmed too easy to miss. */}
            <Polyline
              positions={highlightPositions}
              pathOptions={{ color: "var(--color-surface)", weight: 11, opacity: 0.9 }}
            />
            <Polyline
              positions={highlightPositions}
              pathOptions={{ color: "var(--color-load)", weight: 6, opacity: 1 }}
            />
          </>
        )}
        <CircleMarker
          center={[start.lat, start.lon]}
          radius={6}
          pathOptions={{ color: "var(--color-surface)", fillColor: "var(--color-elevation)", fillOpacity: 1, weight: 2 }}
        />
        <Marker position={[finish.lat, finish.lon]} icon={FINISH_ICON} />
        {marker && (
          <CircleMarker
            center={[marker.lat, marker.lon]}
            radius={7}
            pathOptions={{ color: "var(--color-surface)", fillColor: "var(--color-pace)", fillOpacity: 1, weight: 2 }}
          />
        )}
      </MapContainer>
      <div className="activity-route-map__legend">
        <span>Faster</span>
        <span className="activity-route-map__legend-bar" />
        <span>Slower</span>
      </div>
    </div>
  );
}
