// Runtime config injected by public/config.js, loaded via a plain <script> tag in index.html
// before the app bundle -- see docs/adr/0008-phase-5-frontend.md.
interface PersevererRuntimeConfig {
  apiBaseUrl: string;
}

interface Window {
  __PERSEVERER_CONFIG__?: PersevererRuntimeConfig;
}
