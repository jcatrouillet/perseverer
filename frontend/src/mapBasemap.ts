// CARTO vector basemap style URLs, shared by every map surface (ActivityMap thumbnail,
// ActivityRouteMap detail view, MapExplorerPage). See components/CartoBasemapLayer.tsx for the
// react-leaflet layer that actually renders one of these styles, and mapTiles.ts for the one
// remaining *raster* CARTO consumer (GIF-export canvas rasterization -- left on raster tiles
// deliberately, see that file's own docstring).
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

/** Same `?key=` param, same endpoint host, for the one remaining raster CARTO consumer
 * (mapTiles.ts's canvas rasterization). CARTO's key covers both raster and vector services
 * against the same shared quota, so every cartocdn.com request this app makes should carry it. */
export function cartoRasterTileUrlTemplate(style: "light_all" = "light_all"): string {
  const key = cartoApiKey();
  const suffix = key ? `?key=${encodeURIComponent(key)}` : "";
  return `https://{s}.basemaps.cartocdn.com/${style}/{z}/{x}/{y}{r}.png${suffix}`;
}
