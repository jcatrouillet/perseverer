// Runtime config, NOT bundled by Vite -- served as-is by both `npm run dev` and the built
// nginx image, loaded via a plain <script> tag in index.html before the main bundle. This
// committed copy is the local-dev default; the Docker image regenerates this exact file at
// container start from PERSEVERER_API_BASE_URL/PERSEVERER_CARTO_API_KEY (see
// docker/frontend-entrypoint.d and docs/adr/0008-phase-5-frontend.md) -- neither is ever baked
// into the Vite build. Unlike apiBaseUrl, cartoApiKey isn't actually secret (CARTO's basemap
// product is designed to be embedded client-side, like a Mapbox public token), so the committed
// default below is a real usable key, not a placeholder -- see mapBasemap.ts.
window.__PERSEVERER_CONFIG__ = {
  apiBaseUrl: "http://localhost:8008",
  cartoApiKey: "cb1_2idi_1_ce8832025c922954b1c9138e",
};
