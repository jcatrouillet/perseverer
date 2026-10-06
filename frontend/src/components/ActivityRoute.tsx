// The detailed route section on the activity detail page (Phase 7, folding in the user's
// "moving point" playback ask -- a scrubber needs precise timestamped position data, which is a
// detail-view concept, not something the list/day-view thumbnail (ActivityMap.tsx) has room or
// data for). Ties together the pace-coloured map, the per-km splits panel, and a play/scrub
// control, all driven by the activity's own "high" tier stream (fetched separately from the
// "medium" tier ActivityCharts uses -- that multi-panel view doesn't need per-km precision, this
// section does).
import { useEffect, useMemo, useState } from "react";

import type { StreamResponse } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import { downloadBlob, renderRoutePosterBlob } from "../routeExport";
import {
  buildPauseCompressor,
  detectPauseGaps,
  formatClockDuration,
  isPaceSport,
} from "../runningStats";
import { computeKmSplits } from "../splits";
import { ActivityRouteMap, type RoutePoint } from "./ActivityRouteMap";
import { Icon } from "./Icon";
import { SplitsTable } from "./SplitsTable";
import "../styles/activity-route.css";

const POSTER_SIZE = 900;

// Also reused by DayViewActivityRoute.tsx's auto-play-once map, so a route plays back at the
// same pace whether it's this page's manual player or the day view's automatic one.
export const ANIMATION_DURATION_MS = 15000;

export interface RouteData {
  points: RoutePoint[];
  distanceM: (number | null)[];
  elapsedS: number[];
  // The same elapsed time as `elapsedS`, but *without* pause compression -- i.e. real wall-clock
  // seconds since the activity's own first recorded sample, matching what the backend's trim
  // endpoint (activity_trim.py::_recompute_window) actually expects for trim_start_s/trim_end_s.
  // `elapsedS` is deliberately compressed for map/slider display (see below); anything that needs
  // to hand a boundary back to the backend must use this parallel array instead, indexed
  // identically -- see ActivityTrimControls.tsx's own commit path for why this matters.
  rawElapsedS: number[];
  altitudeM: (number | null)[] | undefined;
}

const EMPTY_ROUTE: RouteData = {
  points: [],
  distanceM: [],
  elapsedS: [],
  rawElapsedS: [],
  altitudeM: undefined,
};

/** Reduces a raw stream response down to the parallel lat/lon/distance/elapsed-time arrays the
 * route map and splits table need -- shared with DayViewActivityRoute.tsx's compact auto-play
 * map, so both read the exact same per-point data the activity detail page's own route uses. */
export function buildRouteData(stream: StreamResponse): RouteData {
  const lat = stream.series.lat;
  const lon = stream.series.lon;
  if (lat == null || lon == null || stream.timestamps.length === 0) return EMPTY_ROUTE;

  const distanceSeries = stream.series.distance_m;
  const altitudeSeries = stream.series.altitude_m;
  const startMs = new Date(stream.timestamps[0]!).getTime();
  const rawElapsedAll = stream.timestamps.map((t) => (new Date(t).getTime() - startMs) / 1000);
  // A device pause shows up as a big gap between two consecutive recorded samples -- compressed
  // out for the same reason and the same way ActivityCharts.tsx does it: a straight line/split
  // drawn across dead time nothing was recorded for otherwise shows a wildly wrong per-km pace
  // for whichever km happened to contain the pause (confirmed against a real paused activity).
  const compress = buildPauseCompressor(rawElapsedAll, detectPauseGaps(rawElapsedAll));

  const points: RoutePoint[] = [];
  const distanceM: (number | null)[] = [];
  const elapsedS: number[] = [];
  const rawElapsedS: number[] = [];
  const altitudeM: (number | null)[] = [];

  for (let i = 0; i < stream.timestamps.length; i++) {
    const la = lat[i];
    const lo = lon[i];
    if (la == null || lo == null) continue;
    points.push({ lat: la, lon: lo });
    distanceM.push(distanceSeries?.[i] ?? null);
    elapsedS.push(compress(rawElapsedAll[i]!));
    rawElapsedS.push(rawElapsedAll[i]!);
    altitudeM.push(altitudeSeries?.[i] ?? null);
  }

  return {
    points,
    distanceM,
    elapsedS,
    rawElapsedS,
    altitudeM: altitudeSeries != null ? altitudeM : undefined,
  };
}

export function ActivityRoute({ stream, sport }: { stream: StreamResponse; sport: string }) {
  const route = useMemo(() => buildRouteData(stream), [stream]);
  const splits = useMemo(
    () => computeKmSplits(route.distanceM, route.elapsedS, route.altitudeM),
    [route],
  );
  const paceSport = isPaceSport(sport);
  const { metersToDisplay, unitLabel, formatPace, kmhToDisplay, speedUnitLabel } =
    useDistanceFormat();

  const [hoveredKm, setHoveredKm] = useState<number | null>(null);
  const [progress, setProgress] = useState(0);
  const [playing, setPlaying] = useState(false);
  // null = not exporting; 0..1 = gif.js's own encoding progress. Frame generation itself is
  // fast (plain canvas draws); encoding is the part worth showing progress for.
  const [gifProgress, setGifProgress] = useState<number | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    if (!isFullscreen) return undefined;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsFullscreen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    // Matches the map's own overlay covering the page -- without this, the page underneath
    // stays scrollable behind it, which reads as broken rather than "still there".
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [isFullscreen]);

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

  // The activity's own totals, for the poster export's stats block -- last recorded point's
  // cumulative distance/elapsed time, same "effective end of activity" reading the rest of the
  // UI uses, not a re-derivation.
  const totalDistanceM = [...route.distanceM].reverse().find((d) => d != null) ?? null;
  const totalDurationS = route.elapsedS[route.elapsedS.length - 1] ?? null;

  const exportPoster = async () => {
    if (totalDistanceM == null || totalDurationS == null || totalDurationS <= 0) return;
    const distanceKm = totalDistanceM / 1000;
    const paceLabel = paceSport
      ? formatPace(totalDurationS / distanceKm)
      : `${kmhToDisplay(distanceKm / (totalDurationS / 3600)).toFixed(1)} ${speedUnitLabel}`;
    const blob = await renderRoutePosterBlob(
      POSTER_SIZE,
      POSTER_SIZE,
      route.points,
      route.distanceM,
      route.elapsedS,
      {
        distanceLabel: `Distance: ${metersToDisplay(totalDistanceM).toFixed(2)} ${unitLabel}`,
        durationLabel: `Time: ${formatClockDuration(totalDurationS)}`,
        paceLabel: `Pace: ${paceLabel}`,
      },
    );
    if (blob) downloadBlob(blob, "route.png");
  };

  const exportGif = async () => {
    if (totalDistanceM == null || totalDurationS == null || totalDurationS <= 0) return;
    const distanceKm = totalDistanceM / 1000;
    const paceLabel = paceSport
      ? formatPace(totalDurationS / distanceKm)
      : `${kmhToDisplay(distanceKm / (totalDurationS / 3600)).toFixed(1)} ${speedUnitLabel}`;
    setGifProgress(0);
    try {
      // Dynamically imported: gif.js pulls in its own Web Worker asset and is only ever needed
      // once a user actually asks for a GIF, not on every activity-detail page load.
      const { renderRouteGif } = await import("../routeGif");
      const blob = await renderRouteGif(
        route.points,
        route.distanceM,
        route.elapsedS,
        {
          distanceLabel: `Distance: ${metersToDisplay(totalDistanceM).toFixed(2)} ${unitLabel}`,
          durationLabel: `Time: ${formatClockDuration(totalDurationS)}`,
          paceLabel: `Pace: ${paceLabel}`,
        },
        setGifProgress,
      );
      downloadBlob(blob, "route.gif");
    } finally {
      setGifProgress(null);
    }
  };

  return (
    <section className="card activity-route">
      <h2>Route</h2>
      <div className="activity-route__layout">
        <div
          className={`activity-route__map-col${isFullscreen ? " activity-route__map-col--fullscreen" : ""}`}
        >
          <button
            type="button"
            className="button activity-route__fullscreen-btn"
            onClick={() => setIsFullscreen((f) => !f)}
            aria-label={isFullscreen ? "Exit fullscreen map" : "View map fullscreen"}
          >
            <Icon name={isFullscreen ? "collapse" : "expand"} />
          </button>
          <ActivityRouteMap
            points={route.points}
            distanceM={route.distanceM}
            elapsedS={route.elapsedS}
            highlightRange={highlightRange}
            markerIndex={currentIndex}
            resizeSignal={isFullscreen}
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
              {currentDistanceM != null &&
                ` · ${metersToDisplay(currentDistanceM).toFixed(2)} ${unitLabel}`}
            </span>
          </div>
          <div className="activity-route__export-row">
            <button type="button" className="button" onClick={exportPoster}>
              <Icon name="download" />
              Export image
            </button>
            <button
              type="button"
              className="button"
              onClick={exportGif}
              disabled={gifProgress != null}
            >
              <Icon name="download" />
              {gifProgress != null ? `Encoding… ${Math.round(gifProgress * 100)}%` : "Export GIF"}
            </button>
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
