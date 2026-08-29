// CARTO basemap URLs (vector style JSON and raster tile template), shared by every map surface.
// The vector style is used by ActivityRouteMap.tsx's detail view and MapExplorerPage -- each is
// the only map on its page, so its own MapLibre GL WebGL canvas (components/CartoBasemapLayer.tsx)
// is the only one competing for Chrome's ~16-live-context-per-page ceiling. ActivityMap.tsx's
// thumbnail deliberately stays on the raster template instead: an activity list page renders one
// thumbnail per activity (31 on a real page), and 31 WebGL contexts on one page silently exceeds
// that ceiling -- confirmed live, the browser evicts the oldest contexts with no error, and an
// evicted canvas never recovers. Same for mapTiles.ts's GIF-export canvas rasterizer, for the
// unrelated reason documented in that file's own docstring (raster tiles are a plain
// canvas.drawImage(), a vector/WebGL basemap needs an actual render pass).
//
// The key is delivered the same runtime-configured way apiBaseUrl already is (public/config.js,
// regenerated at container start -- see docker/frontend-entrypoint.d/20-generate-config.sh) --
// never baked into the Vite build, even though (unlike apiBaseUrl) this key isn't actually
// secret: CARTO's own basemap product is designed to be embedded client-side, the same way a
// Mapbox public token or Google Maps API key is. Runtime-config still wins over a build-time env
// var here because it lets bercy's own key be rotated (CARTO's free tier is a shared 5M
// tiles/month fair-use ceiling) without a rebuild, matching every other prod-tunable value in
// this app.
export type CartoBasemapStyle = "positron" | "voyager" | "dark-matter";

const STYLE_PATH: Record<CartoBasemapStyle, string> = {
  positron: "positron-gl-style",
  voyager: "voyager-gl-style",
  "dark-matter": "dark-matter-gl-style",
};

export function cartoApiKey(): string | undefined {
  return window.__PERSEVERER_CONFIG__?.cartoApiKey || undefined;
}

/** Vector style JSON URL for one of CARTO's basemap styles (MapLibre GL style spec). Confirmed
 * against CARTO's own docs (docs.carto.com/faqs/carto-basemaps): `?key=` is the exact query
 * param name, and it's required once calling basemaps.cartocdn.com directly rather than through
 * the CARTO platform itself -- omitted gracefully (not thrown) when unset so a dev checkout
 * without a key configured still renders *something*, even if CARTO's own fair-use limit ends up
 * rejecting the anonymous request. */
export function cartoStyleUrl(style: CartoBasemapStyle): string {
  const base = `https://basemaps.cartocdn.com/gl/${STYLE_PATH[style]}/style.json`;
  const key = cartoApiKey();
  return key ? `${base}?key=${encodeURIComponent(key)}` : base;
}

/** Same `?key=` param, same endpoint host, for this app's raster CARTO consumers
 * (ActivityMap.tsx's thumbnail, mapTiles.ts's GIF-export canvas rasterizer). CARTO's key covers
 * both raster and vector services against the same shared quota, so every cartocdn.com request
 * this app makes should carry it. */
export function cartoRasterTileUrlTemplate(style: "light_all" = "light_all"): string {
  const key = cartoApiKey();
  const suffix = key ? `?key=${encodeURIComponent(key)}` : "";
  return `https://{s}.basemaps.cartocdn.com/${style}/{z}/{x}/{y}{r}.png${suffix}`;
}
