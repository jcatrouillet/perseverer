// The detailed, pace-coloured route map for the activity detail page (distinct from
// ActivityMap.tsx's non-interactive list/day-view thumbnail -- see that file's own docstring for
// why they're separate components). Colours each segment of the route by how fast it was run
// relative to the rest of the activity (blue = faster, red = slower, matching the reference
// image's legend), supports highlighting one split's segment when its row is hovered in
// SplitsTable, and can show a single moving marker for the playback animation in ActivityRoute.
import "leaflet/dist/leaflet.css";
import type { LatLngBoundsExpression, LatLngTuple } from "leaflet";
import { useEffect, useMemo, useState } from "react";
import { CircleMarker, MapContainer, Polyline, TileLayer } from "react-leaflet";

import { hexToRgb, lerpColor, type Rgb } from "../colorGradient";
import "../styles/activity-route.css";

export interface RoutePoint {
  lat: number;
  lon: number;
}

interface Segment {
  positions: LatLngTuple[];
  color: string;
}

// Capping the rendered segment count keeps this smooth even on a multi-hour activity's ~20,000-
// point high-tier stream -- a gradient this fine-grained reads as continuous at map scale anyway.
const TARGET_SEGMENTS = 250;
// Below this, a GPS auto-pause/stopped-at-a-light chunk reads as "infinitely slow" and would pin
// the whole colour scale to its red end -- matches streamSpeedValue's own stationary floor.
const MIN_MOVING_SPEED_MPS = 0.3;

function resolveTone(cssVar: string): Rgb {
  const value = getComputedStyle(document.documentElement).getPropertyValue(cssVar).trim();
  return hexToRgb(value);
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor(p * (sorted.length - 1))));
  return sorted[idx]!;
}

function buildSegments(
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
    };
  });
}

export function ActivityRouteMap({
  points,
  distanceM,
  elapsedS,
  highlightRange,
  markerIndex,
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
}) {
  const [tones, setTones] = useState<{ slow: Rgb; fast: Rgb } | null>(null);

  useEffect(() => {
    setTones({ slow: resolveTone("--color-heart-rate"), fast: resolveTone("--color-pace") });
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
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
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
        <CircleMarker
          center={[finish.lat, finish.lon]}
          radius={6}
          pathOptions={{ color: "var(--color-surface)", fillColor: "var(--color-text-muted)", fillOpacity: 1, weight: 2 }}
        />
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
