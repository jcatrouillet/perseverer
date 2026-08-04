// Runtime config, NOT bundled by Vite -- served as-is by both `npm run dev` and the built
// nginx image, loaded via a plain <script> tag in index.html before the main bundle. This
// committed copy is the local-dev default; the Docker image regenerates this exact file at
// container start from SPORTHEALTH_API_BASE_URL (see docker/frontend-entrypoint.d and
// docs/adr/0008-phase-5-frontend.md) -- it is never baked into the Vite build.
window.__SPORTHEALTH_CONFIG__ = {
  apiBaseUrl: "http://localhost:8008",
};
