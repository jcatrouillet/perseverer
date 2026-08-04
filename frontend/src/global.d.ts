// Runtime config injected by public/config.js, loaded via a plain <script> tag in index.html
// before the app bundle -- see docs/adr/0008-phase-5-frontend.md.
interface SportHealthRuntimeConfig {
  apiBaseUrl: string;
}

interface Window {
  __SPORTHEALTH_CONFIG__?: SportHealthRuntimeConfig;
}
