// "An export option for the animation of the dot along the path of the activity, generate a
// GIF" -- client-side, no server round-trip. "For the GIF export, keep the map background" --
// unlike the PNG poster (deliberately tile-free so the trace can be laid over any photo), the
// GIF rasterizes the real CARTO Positron basemap once via mapTiles.ts and reuses it as every
// frame's backdrop. Reuses routeExport.ts's own trace-drawing (through the same projection the
// tiles were drawn with, so the route lines up pixel-exact with the map underneath it), adding
// a moving dot per frame. Verified installable/buildable with this project's React 19/Vite +
// TS-strict setup before being relied on (gif.js + @types/gif.js, per this project's standing
// "verify a vendor library's real behavior" discipline) -- `npm run typecheck` and `npm run
// build` both pass with this import in place.
//
// gif.js encodes off the main thread via a Web Worker it spins up itself; the worker script is
// gif.js's own dist/gif.worker.js, which Vite's `?url` suffix resolves to a fingerprinted static
// asset URL at build time -- gif.js is given that URL via `workerScript`, not bundled inline.
import GIF from "gif.js";
import gifWorkerUrl from "gif.js/dist/gif.worker.js?url";

import type { RoutePoint } from "./components/ActivityRouteMap";
import { resolveTone } from "./components/ActivityRouteMap";
import { drawBasemapTiles } from "./mapTiles";
import { drawOutlinedText, drawRouteTrace, POSTER_PADDING, type ProjectedPoint, type RoutePosterStats } from "./routeExport";

const GIF_SIZE = 640;
const STATS_LINE_HEIGHT = 26;
const FRAME_COUNT = 60;
// Matches ActivityRoute.tsx's own ANIMATION_DURATION_MS (15s) so the exported GIF plays back at
// the same pace as the in-app playback the user is exporting.
const TOTAL_DURATION_MS = 15000;
const FRAME_DELAY_MS = Math.round(TOTAL_DURATION_MS / FRAME_COUNT);
const MARKER_RADIUS = 9;

/** Renders a `points.length`-point route as an animated GIF over the real map basemap: the
 * tiles + full pace-coloured trace drawn once as a shared backdrop (tiles don't change frame to
 * frame, only the moving dot does -- fetching them once and reusing the rasterized result, not
 * once per frame, is what keeps this from re-requesting the same tiles 60 times), plus a dot
 * moving along the trace over `FRAME_COUNT` frames. `onProgress` reports gif.js's own 0..1
 * encoding progress (encoding, not frame generation, is the slow part).
 */
export async function renderRouteGif(
  points: RoutePoint[],
  distanceM: (number | null)[],
  elapsedS: number[],
  stats: RoutePosterStats,
  onProgress?: (fraction: number) => void,
): Promise<Blob> {
  if (points.length < 2) {
    throw new Error("need at least 2 route points");
  }

  const background = document.createElement("canvas");
  background.width = GIF_SIZE;
  background.height = GIF_SIZE;
  const bgCtx = background.getContext("2d");
  if (!bgCtx) throw new Error("2D canvas context unavailable");

  // A fallback fill behind the tiles -- if a particular tile request fails (network hiccup),
  // that square shows the app's own surface colour instead of being left fully transparent
  // (which a GIF, having no real alpha channel, would otherwise render as an arbitrary colour).
  const surfaceRgb = resolveTone("--color-surface");
  bgCtx.fillStyle = `rgb(${surfaceRgb[0]}, ${surfaceRgb[1]}, ${surfaceRgb[2]})`;
  bgCtx.fillRect(0, 0, GIF_SIZE, GIF_SIZE);

  const tiles = await drawBasemapTiles(bgCtx, points, GIF_SIZE, GIF_SIZE, POSTER_PADDING);
  const projected: ProjectedPoint[] = points.map((p) => tiles.project(p));
  drawRouteTrace(bgCtx, points, projected, distanceM, elapsedS);

  const canvas = document.createElement("canvas");
  canvas.width = GIF_SIZE;
  canvas.height = GIF_SIZE;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D canvas context unavailable");

  const markerRgb = resolveTone("--color-pace");

  const gif = new GIF({
    workers: 2,
    quality: 10,
    width: GIF_SIZE,
    height: GIF_SIZE,
    workerScript: gifWorkerUrl,
  });

  ctx.font = "600 20px system-ui, sans-serif";
  ctx.textBaseline = "alphabetic";

  for (let i = 0; i < FRAME_COUNT; i++) {
    const progress = i / (FRAME_COUNT - 1);
    const markerIndex = Math.round(progress * (points.length - 1));

    ctx.clearRect(0, 0, GIF_SIZE, GIF_SIZE);
    ctx.drawImage(background, 0, 0);

    const marker = projected[markerIndex];
    if (marker) {
      ctx.beginPath();
      ctx.arc(marker.x, marker.y, MARKER_RADIUS, 0, Math.PI * 2);
      ctx.fillStyle = `rgb(${markerRgb[0]}, ${markerRgb[1]}, ${markerRgb[2]})`;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();
    }

    // Same bottom-up stacking as routeExport.ts::drawRoutePoster -- distance, time, then pace,
    // anchored so the last line sits POSTER_PADDING above the bottom edge regardless of how many
    // lines there are.
    const lines = [stats.distanceLabel, stats.durationLabel, stats.paceLabel];
    lines.forEach((line, i) => {
      drawOutlinedText(
        ctx,
        line,
        POSTER_PADDING,
        GIF_SIZE - POSTER_PADDING + i * STATS_LINE_HEIGHT - (lines.length - 1) * STATS_LINE_HEIGHT,
      );
    });

    gif.addFrame(ctx, { copy: true, delay: FRAME_DELAY_MS });
  }

  return new Promise((resolve, reject) => {
    gif.on("progress", (fraction: number) => onProgress?.(fraction));
    gif.once("finished", (blob: Blob) => resolve(blob));
    gif.once("abort", () => reject(new Error("GIF encoding aborted")));
    gif.render();
  });
}
