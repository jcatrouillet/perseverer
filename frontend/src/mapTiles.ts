// Real Web Mercator tile math + a fetch-and-draw helper for rasterizing the same CARTO
// Positron basemap ActivityRouteMap.tsx uses onto a plain <canvas> -- "for the GIF export, keep
// the map background" (unlike the PNG poster, which is deliberately tile-free so the trace can
// be laid over any photo of the user's own choosing). CARTO's tile CDN sends
// `Access-Control-Allow-Origin: *` (confirmed with a direct request before relying on this),
// which is what makes drawing a cross-origin tile image onto a canvas without tainting it --
// and therefore reading pixels back out via gif.js -- possible at all.
import type { RoutePoint } from "./components/ActivityRouteMap";
import type { ProjectedPoint } from "./routeExport";

const TILE_SIZE = 256;
const TILE_SUBDOMAINS = ["a", "b", "c", "d"];
// Capped well below CARTO's own max (~20) -- a canvas-sized viewport only ever needs a handful
// of tiles regardless of zoom (bounded by canvas width/height / 256, not by route size), so the
// cap exists purely to avoid absurdly over-zoomed tiles for a route with a tiny bounding box
// (e.g. a stationary treadmill run), not to bound tile *count*.
const MAX_ZOOM = 17;
const MIN_ZOOM = 1;

export function lonLatToWorldPixel(lon: number, lat: number, zoom: number): ProjectedPoint {
  const scale = TILE_SIZE * 2 ** zoom;
  const x = ((lon + 180) / 360) * scale;
  const latRad = (lat * Math.PI) / 180;
  const y = (0.5 - Math.log(Math.tan(Math.PI / 4 + latRad / 2)) / (2 * Math.PI)) * scale;
  return { x, y };
}

/** The largest zoom at which the bounding box (its NW/SE corners, since Mercator projection is
 * monotonic in both lon and lat) still fits within `availW`x`availH` -- the same "fit bounds"
 * idea Leaflet's own `fitBounds` uses, just computed directly rather than through a map
 * instance, since there is no Leaflet map here. */
export function chooseZoom(
  minLon: number,
  minLat: number,
  maxLon: number,
  maxLat: number,
  availW: number,
  availH: number,
): number {
  for (let zoom = MAX_ZOOM; zoom >= MIN_ZOOM; zoom--) {
    const nw = lonLatToWorldPixel(minLon, maxLat, zoom);
    const se = lonLatToWorldPixel(maxLon, minLat, zoom);
    if (Math.abs(se.x - nw.x) <= availW && Math.abs(se.y - nw.y) <= availH) return zoom;
  }
  return MIN_ZOOM;
}

function loadImage(url: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    // A missing/failed tile leaves that square blank rather than aborting the whole export --
    // matches Leaflet's own graceful-degradation behaviour for a 404/network-flaky tile.
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

export interface TileProjection {
  /** Projects a route point into the same canvas pixel space the tiles were just drawn into,
   * so a trace/marker drawn afterwards lines up with the basemap underneath it. */
  project(point: RoutePoint): ProjectedPoint;
}

/** Fetches and draws every basemap tile covering `points`' bounding box (centred, zoomed to
 * fit within `width`x`height` minus `padding`) onto `ctx`, then returns a projection function
 * for drawing the route/marker in the same pixel space. Awaits every tile load before
 * resolving, so the caller can rely on the background being fully painted synchronously
 * afterwards (important for a GIF export, which reuses this one rasterized background across
 * every frame rather than re-fetching tiles per frame). */
export async function drawBasemapTiles(
  ctx: CanvasRenderingContext2D,
  points: RoutePoint[],
  width: number,
  height: number,
  padding: number,
): Promise<TileProjection> {
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

  const availW = Math.max(width - padding * 2, 1);
  const availH = Math.max(height - padding * 2, 1);
  const zoom = chooseZoom(minLon, minLat, maxLon, maxLat, availW, availH);

  const centerLon = (minLon + maxLon) / 2;
  const centerLat = (minLat + maxLat) / 2;
  const centerPx = lonLatToWorldPixel(centerLon, centerLat, zoom);
  const originX = centerPx.x - width / 2;
  const originY = centerPx.y - height / 2;

  const maxTileIndex = 2 ** zoom - 1;
  const tileMinX = Math.max(0, Math.floor(originX / TILE_SIZE));
  const tileMaxX = Math.min(maxTileIndex, Math.floor((originX + width) / TILE_SIZE));
  const tileMinY = Math.max(0, Math.floor(originY / TILE_SIZE));
  const tileMaxY = Math.min(maxTileIndex, Math.floor((originY + height) / TILE_SIZE));

  const loads: Promise<void>[] = [];
  for (let tx = tileMinX; tx <= tileMaxX; tx++) {
    for (let ty = tileMinY; ty <= tileMaxY; ty++) {
      const sub = TILE_SUBDOMAINS[(tx + ty) % TILE_SUBDOMAINS.length];
      const url = `https://${sub}.basemaps.cartocdn.com/light_all/${zoom}/${tx}/${ty}.png`;
      const px = tx * TILE_SIZE - originX;
      const py = ty * TILE_SIZE - originY;
      loads.push(
        loadImage(url).then((img) => {
          if (img) ctx.drawImage(img, px, py);
        }),
      );
    }
  }
  await Promise.all(loads);

  return {
    project(point: RoutePoint): ProjectedPoint {
      const p = lonLatToWorldPixel(point.lon, point.lat, zoom);
      return { x: p.x - originX, y: p.y - originY };
    },
  };
}
