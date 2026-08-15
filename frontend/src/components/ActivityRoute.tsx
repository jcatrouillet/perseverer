// The detailed route section on the activity detail page (Phase 7, folding in the user's
// "moving point" playback ask -- a scrubber needs precise timestamped position data, which is a
// detail-view concept, not something the list/day-view thumbnail (ActivityMap.tsx) has room or
// data for). Ties together the pace-coloured map, the per-km splits panel, and a play/scrub
// control, all driven by the activity's own "high" tier stream (fetched separately from the
// "medium" tier ActivityCharts uses -- that multi-panel view doesn't need per-km precision, this
// section does).
import { useEffect, useMemo, useState } from "react";

import type { StreamResponse } from "../api/types";
import { formatClockDuration, isPaceSport } from "../runningStats";
import { computeKmSplits } from "../splits";
import { ActivityRouteMap, type RoutePoint } from "./ActivityRouteMap";
import { Icon } from "./Icon";
import { SplitsTable } from "./SplitsTable";
import "../styles/activity-route.css";

const ANIMATION_DURATION_MS = 15000;

interface RouteData {
  points: RoutePoint[];
  distanceM: (number | null)[];
  elapsedS: number[];
  altitudeM: (number | null)[] | undefined;
}

const EMPTY_ROUTE: RouteData = { points: [], distanceM: [], elapsedS: [], altitudeM: undefined };

function buildRouteData(stream: StreamResponse): RouteData {
  const lat = stream.series.lat;
  const lon = stream.series.lon;
  if (lat == null || lon == null || stream.timestamps.length === 0) return EMPTY_ROUTE;

  const distanceSeries = stream.series.distance_m;
  const altitudeSeries = stream.series.altitude_m;
  const startMs = new Date(stream.timestamps[0]!).getTime();

  const points: RoutePoint[] = [];
  const distanceM: (number | null)[] = [];
  const elapsedS: number[] = [];
  const altitudeM: (number | null)[] = [];

  for (let i = 0; i < stream.timestamps.length; i++) {
    const la = lat[i];
    const lo = lon[i];
    if (la == null || lo == null) continue;
    points.push({ lat: la, lon: lo });
    distanceM.push(distanceSeries?.[i] ?? null);
    elapsedS.push((new Date(stream.timestamps[i]!).getTime() - startMs) / 1000);
    altitudeM.push(altitudeSeries?.[i] ?? null);
  }

  return { points, distanceM, elapsedS, altitudeM: altitudeSeries != null ? altitudeM : undefined };
}

export function ActivityRoute({ stream, sport }: { stream: StreamResponse; sport: string }) {
  const route = useMemo(() => buildRouteData(stream), [stream]);
  const splits = useMemo(
    () => computeKmSplits(route.distanceM, route.elapsedS, route.altitudeM),
    [route],
  );
  const paceSport = isPaceSport(sport);

  const [hoveredKm, setHoveredKm] = useState<number | null>(null);
  const [progress, setProgress] = useState(0);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!playing) return undefined;
    let raf = 0;
    const startTime = performance.now() - progress * ANIMATION_DURATION_MS;
    const step = (now: number) => {
      const p = (now - startTime) / ANIMATION_DURATION_MS;
      if (p >= 1) {
        setProgress(1);
        setPlaying(false);
        return;
      }
      setProgress(p);
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    // `progress` is deliberately not a dependency: it's this effect's own output, not an input --
    // depending on it would restart the RAF loop from a fresh closure every frame instead of
    // animating. Only starting/stopping playback should re-trigger the effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  if (route.points.length < 2) return null;

  const hoveredSplit = splits.find((s) => s.km === hoveredKm);
  const highlightRange: [number, number] | null = hoveredSplit
    ? [hoveredSplit.startIndex, hoveredSplit.endIndex]
    : null;

  const currentIndex = Math.round(progress * (route.points.length - 1));
  const currentElapsedS = route.elapsedS[currentIndex] ?? 0;
  const currentDistanceM = route.distanceM[currentIndex];

  return (
    <section className="card activity-route">
      <h2>Route</h2>
      <div className="activity-route__layout">
        <div className="activity-route__map-col">
          <ActivityRouteMap
            points={route.points}
            distanceM={route.distanceM}
            elapsedS={route.elapsedS}
            highlightRange={highlightRange}
            markerIndex={currentIndex}
          />
          <div className="activity-route__player">
            <button
              type="button"
              className="button"
              onClick={() => setPlaying((p) => !p)}
              aria-label={playing ? "Pause" : "Play"}
            >
              <Icon name={playing ? "pause" : "play"} />
            </button>
            <input
              type="range"
              min={0}
              max={1000}
              value={Math.round(progress * 1000)}
              onChange={(e) => {
                setPlaying(false);
                setProgress(Number(e.target.value) / 1000);
              }}
              className="activity-route__scrubber"
              aria-label="Route playback position"
            />
            <span className="activity-route__player-readout">
              {formatClockDuration(currentElapsedS)}
              {currentDistanceM != null && ` · ${(currentDistanceM / 1000).toFixed(2)} km`}
            </span>
          </div>
        </div>
        <div className="activity-route__splits-col">
          <SplitsTable
            splits={splits}
            paceSport={paceSport}
            hoveredKm={hoveredKm}
            onHoverKm={setHoveredKm}
          />
        </div>
      </div>
    </section>
  );
}
