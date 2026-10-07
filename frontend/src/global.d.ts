// Runtime config injected by public/config.js, loaded via a plain <script> tag in index.html
// before the app bundle -- docs/ARCHITECTURE.md.
interface PersevererRuntimeConfig {
  apiBaseUrl: string;
  /** CARTO basemap API key (see mapBasemap.ts) -- optional, unlike apiBaseUrl, since a map with
   * no key still renders (just against CARTO's anonymous-tier limits) rather than failing the
   * whole app closed. */
  cartoApiKey?: string;
}

interface Window {
  __PERSEVERER_CONFIG__?: PersevererRuntimeConfig;
}
