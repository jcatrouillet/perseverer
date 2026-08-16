// "An export for the trace of the activity on the map, without the map background, just the
// trace with a transparent background and the key stats" -- a canvas-based poster, entirely
// independent of Leaflet/map tiles. Projects lat/lon straight to canvas coordinates via a
// simple fit-to-bounds linear scale (with a cos(latitude) correction so the route's real shape
// isn't stretched east-west), reusing ActivityRouteMap's own pace-gradient segment-colouring
// logic so the poster's colours always match the on-page map exactly.
import { buildSegments, resolveTone, type RoutePoint } from "./components/ActivityRouteMap";

export interface ProjectedPoint {
  x: number;
  y: number;
}

/** Fits `points` into a `width`x`height` canvas (minus `padding` on every side), preserving
 * the route's real aspect ratio -- a naive lat/lon-to-pixel mapping without the cos(latitude)
 * correction would stretch an east-west route at higher latitudes, since a degree of longitude
 * covers less real distance than a degree of latitude away from the equator. */
export function projectPoints(
  points: RoutePoint[],
  width: number,
  height: number,
  padding: number,
): ProjectedPoint[] {
  if (points.length === 0) return [];

  let minLat = points[0]!.lat;
  let maxLat = points[0]!.lat;
  let minLon = points[0]!.lon;
  let maxLon = points[0]!.lon;
  for (const p of points) {
    if (p.lat < minLat) minLat = p.lat;
    if (p.lat > maxLat) maxLat = p.lat;
    if (p.lon < minLon) minLon = p.lon;
    if (p.lon > maxLon) maxLon = p.lon;
  }

  const lonScale = Math.cos(((minLat + maxLat) / 2) * (Math.PI / 180));
  const spanLat = Math.max(maxLat - minLat, 1e-9);
  const spanLon = Math.max((maxLon - minLon) * lonScale, 1e-9);

  const availW = Math.max(width - padding * 2, 1);
  const availH = Math.max(height - padding * 2, 1);
  const scale = Math.min(availW / spanLon, availH / spanLat);

  const drawnW = spanLon * scale;
  const drawnH = spanLat * scale;
  const offsetX = padding + (availW - drawnW) / 2;
  const offsetY = padding + (availH - drawnH) / 2;

  return points.map((p) => ({
    x: offsetX + (p.lon - minLon) * lonScale * scale,
    // Canvas y grows downward; latitude grows northward -- flip so north is up.
    y: offsetY + (maxLat - p.lat) * scale,
  }));
}

export interface RoutePosterStats {
  distanceLabel: string;
  durationLabel: string;
  paceLabel: string;
}

export const POSTER_PADDING = 56;
const LINE_WIDTH = 5;
const TEXT_OUTLINE_WIDTH = 5;

/** White fill with a thick black outline, readable against literally anything underneath it --
 * a plain single-colour fill (the poster's original approach) reads fine against a mid-tone
 * background but disappears against a light photo (white-on-white) or a dark one
 * (near-black-on-black) once the transparent-background PNG is laid over the user's own image,
 * or, for the GIF export, against whichever basemap tile colour happens to sit under the text.
 * Stroke drawn first so the fill sits cleanly on top of it, not the other way round. */
export function drawOutlinedText(ctx: CanvasRenderingContext2D, text: string, x: number, y: number): void {
  ctx.lineJoin = "round";
  ctx.miterLimit = 2;
  ctx.lineWidth = TEXT_OUTLINE_WIDTH;
  ctx.strokeStyle = "#000000";
  ctx.strokeText(text, x, y);
  ctx.fillStyle = "#ffffff";
  ctx.fillText(text, x, y);
}

/** Draws just the pace-coloured trace (no background fill, no stats text) using an
 * already-projected point set -- the part shared between the static PNG poster
 * (drawRoutePoster, below, which projects via the simple fit-to-bounds `projectPoints` above)
 * and the GIF export's per-frame renderer (routeGif.ts, which instead projects through real
 * Web Mercator tile math via `mapTiles.ts::drawBasemapTiles`, so its trace aligns pixel-exact
 * with the map tiles drawn underneath it). Taking `projected` as a parameter rather than
 * computing it internally is what lets both callers share this same segment-drawing loop
 * despite using two different projections. */
export function drawRouteTrace(
  ctx: CanvasRenderingContext2D,
  points: RoutePoint[],
  projected: ProjectedPoint[],
  distanceM: (number | null)[],
  elapsedS: number[],
): void {
  const slowRgb = resolveTone("--color-pace");
  const fastRgb = resolveTone("--color-heart-rate");
  const segments = buildSegments(points, distanceM, elapsedS, slowRgb, fastRgb);

  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = LINE_WIDTH;
  for (const seg of segments) {
    const legPoints = projected.slice(seg.start, seg.end + 1);
    if (legPoints.length < 2) continue;
    ctx.beginPath();
    ctx.moveTo(legPoints[0]!.x, legPoints[0]!.y);
    for (const p of legPoints.slice(1)) ctx.lineTo(p.x, p.y);
    ctx.strokeStyle = seg.color;
    ctx.stroke();
  }
}

/** Draws the pace-coloured trace plus a small stats block onto `ctx`, background left fully
 * transparent (no `fillRect` before drawing) -- the whole point of this export is a trace the
 * user can lay over any photo/background of their own choosing. */
export function drawRoutePoster(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  points: RoutePoint[],
  distanceM: (number | null)[],
  elapsedS: number[],
  stats: RoutePosterStats,
): void {
  ctx.clearRect(0, 0, width, height);
  if (points.length < 2) return;

  const projected = projectPoints(points, width, height, POSTER_PADDING);
  drawRouteTrace(ctx, points, projected, distanceM, elapsedS);

  ctx.font = "600 22px system-ui, sans-serif";
  ctx.textBaseline = "alphabetic";
  const lines = [stats.distanceLabel, stats.durationLabel, stats.paceLabel];
  const lineHeight = 28;
  lines.forEach((line, i) => {
    drawOutlinedText(
      ctx,
      line,
      POSTER_PADDING,
      height - POSTER_PADDING + i * lineHeight - (lines.length - 1) * lineHeight,
    );
  });
}

/** Renders the poster onto an offscreen canvas at `width`x`height` and returns it as a PNG
 * blob (PNG, not JPEG, specifically to keep the transparent background the user asked for --
 * JPEG has no alpha channel). */
export function renderRoutePosterBlob(
  width: number,
  height: number,
  points: RoutePoint[],
  distanceM: (number | null)[],
  elapsedS: number[],
  stats: RoutePosterStats,
): Promise<Blob | null> {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return Promise.resolve(null);
  drawRoutePoster(ctx, width, height, points, distanceM, elapsedS, stats);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

/** Triggers a real browser download -- this runs inside the deployed app itself (not a
 * sandboxed preview), so a plain object-URL anchor-click is the normal, correct approach. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
