// One inline SVG sprite for the whole app. Deliberately hand-rolled rather than pulling an
// icon package: ~3kB of markup against 50kB+ for a dependency, and this project's standing
// posture is one considered dependency per phase (Recharts was Phase 6.1's -- see ADR 0010).
//
// Every glyph is stroke-only on a 24x24 grid and paints with `currentColor`, which is what
// makes the colour system work: a single glyph inherits whichever metric hue its context sets,
// so there is never a second copy of an icon in another colour. Size comes from CSS
// (`.icon` in layout.css), never from a width/height attribute.
//
// The sprite renders once, above the router, in main.tsx -- a <use> can only resolve a symbol
// that's actually in the document, and mounting it outside <AuthGate> means the login screen
// gets icons too.

export type IconName =
  // Metrics
  | "route"
  | "clock"
  | "mountain"
  | "heart"
  | "flame"
  | "calendar"
  | "trophy"
  | "bolt"
  | "sunrise"
  | "moon"
  | "pulse"
  | "gauge"
  | "stairs"
  | "steps"
  | "trend"
  | "thermometer"
  | "battery"
  | "droplet"
  // Sports
  | "run"
  | "walk"
  | "hike"
  | "bike"
  | "dumbbell"
  | "yoga"
  | "climb"
  | "snow"
  | "racket"
  | "waves"
  // Navigation
  | "grid"
  | "list"
  | "map"
  // Route playback
  | "play"
  | "pause"
  | "download"
  | "expand"
  | "collapse"
  // Weather (WMO code groups -- see weatherCode.ts)
  | "sun"
  | "cloud"
  | "rain"
  | "fog"
  | "storm";

export function IconSprite() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true" focusable="false">
      <defs>
        <symbol viewBox="0 0 24 24" id="i-route">
          <circle cx="5.5" cy="18" r="2.3" />
          <circle cx="18.5" cy="6" r="2.3" />
          <path
            d="M7.6 17.1c3.4-1 4.2-3 4.4-5 .2-2.2 1.4-4 4.4-4.8"
            strokeDasharray="2.4 2.6"
          />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-clock">
          <circle cx="12" cy="12" r="8.4" />
          <path d="M12 7.1v5.2l3.4 2" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-mountain">
          <path d="M2.6 19h18.8L14.1 6l-3.5 6.1-2.3-2.7L2.6 19Z" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-heart">
          <path d="M12 20.2s-7.3-4.5-7.3-9.4a4.25 4.25 0 0 1 7.3-2.6 4.25 4.25 0 0 1 7.3 2.6c0 4.9-7.3 9.4-7.3 9.4Z" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-flame">
          <path d="M12.5 3c.4 3.9-3.1 5-3.1 8.3 0 1 .5 1.7.5 2.4 0 .9-.7 1.5-1.5 1.5-.9 0-1.6-.7-1.6-1.9-1 1.3-1.4 2.6-1.4 3.9C5.4 19.9 8.3 22 12 22s6.6-2.1 6.6-4.8c0-5-3.4-6.6-6.1-14.2Z" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-calendar">
          <rect x="3.6" y="5.2" width="16.8" height="15.2" rx="2.6" />
          <path d="M3.6 10h16.8M8.2 3.2v4M15.8 3.2v4" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-trophy">
          <path d="M8.1 3.8h7.8v5.1a3.9 3.9 0 0 1-7.8 0V3.8Z" />
          <path d="M8.1 5.4H5.6a2.5 2.5 0 0 0 2.5 4.4M15.9 5.4h2.5a2.5 2.5 0 0 1-2.5 4.4" />
          <path d="M12 12.9v3.7M8.8 20.2h6.4M10.4 16.6h3.2l1.6 3.6H8.8l1.6-3.6Z" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-bolt">
          <path d="M13.4 2.4 5.9 13.6h5.3l-.6 8 7.5-11.2h-5.3l.6-8Z" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-sunrise">
          <path d="M12 3.4v3.1M5.7 9.3l1.8 1.8M18.3 9.3l-1.8 1.8M2.8 18.4h18.4M8.1 18.4a3.9 3.9 0 0 1 7.8 0" />
          <path d="M8.6 21.2h6.8" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-moon">
          <path d="M20.1 14.4A8.3 8.3 0 0 1 9.7 4a8.5 8.5 0 1 0 10.4 10.4Z" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-pulse">
          <path d="M2.6 12.4h4l2.1-5.6 3.6 11.2 2.4-7 1.6 3.2h5.1" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-gauge">
          <path d="M4 17.6a9 9 0 1 1 16 0" />
          <path d="M12 17.4 15.9 10" />
          <circle cx="12" cy="17.8" r="1.5" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-stairs">
          <path d="M3.4 20.4h4.2v-4.2h4.2V12h4.2V7.8h4.6" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-steps">
          <path d="M8.2 4.4c1.5 0 2.4 1.3 2.4 3.2 0 2.5-.8 4.2-2.4 4.2s-2.4-1.7-2.4-4.2c0-1.9.9-3.2 2.4-3.2ZM8.2 14c1.3 0 2 .7 2 2 0 1.6-.8 2.7-2 2.7s-2-1.1-2-2.7c0-1.3.7-2 2-2ZM16.4 8c1.5 0 2.4 1.3 2.4 3.2 0 2.5-.8 4.2-2.4 4.2S14 13.7 14 11.2C14 9.3 14.9 8 16.4 8ZM16.4 17.6c1.3 0 2 .7 2 2 0 1.6-.8 2.7-2 2.7" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-trend">
          <path d="M3.2 16.6 9 10.8l3.6 3.6 8.2-8.2" />
          <path d="M15.4 6.2h5.4v5.4" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-thermometer">
          <path d="M12 3.6a2.1 2.1 0 0 0-2.1 2.1v8.6a3.6 3.6 0 1 0 4.2 0V5.7A2.1 2.1 0 0 0 12 3.6Z" />
          <path d="M12 10.4v5.4" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-battery">
          <rect x="2.6" y="7.5" width="16" height="9" rx="2" />
          <path d="M20.6 10.5h1v3h-1" />
          <path d="M6.4 10.2v3.6M10 10.2v3.6M13.6 10.2v3.6" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-droplet">
          <path d="M12 3.2s6.2 7 6.2 11.3a6.2 6.2 0 1 1-12.4 0C5.8 10.2 12 3.2 12 3.2Z" />
        </symbol>

        <symbol viewBox="0 0 24 24" id="i-run">
          <circle cx="14.6" cy="4.5" r="2" />
          <path d="M9.1 20.8 11.8 16l-2.5-2.7.9-4.7-3.6 2-1.5 3.6M12.2 16.1l3.5 1.4 1.6 3.3M11.9 8.6l3.5 2 3-.6" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-walk">
          <circle cx="12.9" cy="4.5" r="2" />
          <path d="M9.4 20.9 11.7 15.4l-1.7-3.3.7-3.5-3 1.7-1 2.9M11.9 15.6l2.7 1.4 1.3 3.9" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-hike">
          <circle cx="13.2" cy="4.4" r="1.9" />
          <path d="M8.4 20.8 10.9 15.3l-1.9-3.1.9-3.7-3.5 1.9-1.3 3.1M11.1 15.5l3.3 1.6 1.5 3.7M19.2 4.2v16.6M19.2 7.4l-2.4 1.1" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-bike">
          <circle cx="5.7" cy="16.4" r="3.6" />
          <circle cx="18.3" cy="16.4" r="3.6" />
          <path d="M5.7 16.4 9.8 8.3h4.4M9.9 8.3h3.1l3.4 8.1M14.6 5.4h2.7" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-dumbbell">
          <path d="M3.6 9.4v5.2M6.8 7.3v9.4M17.2 7.3v9.4M20.4 9.4v5.2M6.8 12h10.4" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-yoga">
          <circle cx="12" cy="4.7" r="2" />
          <path d="M12 8.2v4.6M12 12.8c-3.1 0-5.6 1.6-5.6 3.5 0 1.2 1.3 2 2.9 2h5.4c1.6 0 2.9-.8 2.9-2 0-1.9-2.5-3.5-5.6-3.5ZM12 10.1 8.1 12.6M12 10.1l3.9 2.5" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-climb">
          <circle cx="14.1" cy="4.4" r="1.9" />
          <path d="M6.2 20.6 9.9 17l-.7-4.4 3.1-2.5 2.7 2.8 3.5.8M9.2 10.1 5.6 11.4M14.9 14.1l1.5 6.5" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-snow">
          <path d="M12 2.8v18.4M4.2 7.4l15.6 9M19.8 7.4l-15.6 9" />
          <path d="M9.5 4.7 12 2.8l2.5 1.9M9.5 19.3 12 21.2l2.5-1.9M4.6 10.4l-.4-3 3-.3M19.4 13.6l.4 3-3 .3M19.4 10.4l.4-3-3-.3M4.6 13.6l-.4 3 3 .3" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-racket">
          <ellipse cx="10.2" cy="9.2" rx="5.2" ry="6" transform="rotate(-32 10.2 9.2)" />
          <path d="M13.6 13.8 19.6 20.4M7.1 6.1l6.4 6.3M12.6 5.5 8.1 11.9" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-waves">
          <path d="M2.4 8.4c2.4-2.2 4.8-2.2 7.2 0s4.8 2.2 7.2 0 4.8-2.2 4.8 0M2.4 14c2.4-2.2 4.8-2.2 7.2 0s4.8 2.2 7.2 0 4.8-2.2 4.8 0M2.4 19.6c2.4-2.2 4.8-2.2 7.2 0s4.8 2.2 7.2 0 4.8-2.2 4.8 0" />
        </symbol>

        <symbol viewBox="0 0 24 24" id="i-grid">
          <rect x="3.4" y="3.4" width="7.2" height="7.2" rx="1.8" />
          <rect x="13.4" y="3.4" width="7.2" height="7.2" rx="1.8" />
          <rect x="3.4" y="13.4" width="7.2" height="7.2" rx="1.8" />
          <rect x="13.4" y="13.4" width="7.2" height="7.2" rx="1.8" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-list">
          <path d="M8.4 6.4h12M8.4 12h12M8.4 17.6h12M3.8 6.4h.01M3.8 12h.01M3.8 17.6h.01" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-map">
          <path d="M9 4.4 3.6 6.2v13.4L9 17.8l6 1.8 5.4-1.8V4.4L14.4 6.2 9 4.4Z" />
          <path d="M9 4.4v13.4M15 6.2v13.4" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-play">
          <path d="M6.5 4.2v15.6l13.2-7.8L6.5 4.2Z" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-pause">
          <path d="M6.8 4.4h4v15.2h-4zM13.2 4.4h4v15.2h-4z" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-download">
          <path d="M12 3.6v11.2M7.6 10.4 12 14.8l4.4-4.4" />
          <path d="M4.4 17.2v2a1.8 1.8 0 0 0 1.8 1.8h11.6a1.8 1.8 0 0 0 1.8-1.8v-2" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-expand">
          <path d="M9 3.6H4.4V8.2M15 3.6h4.6V8.2M9 20.4H4.4v-4.6M15 20.4h4.6v-4.6" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-collapse">
          <path d="M4.4 8.6H9V4M19.6 8.6H15V4M4.4 15.4H9V20M19.6 15.4H15V20" />
        </symbol>

        <symbol viewBox="0 0 24 24" id="i-sun">
          <circle cx="12" cy="12" r="4.6" />
          <path d="M12 2.6v3M12 18.4v3M4.6 12h-3M22.4 12h-3M6.3 6.3 4.2 4.2M19.8 19.8l-2.1-2.1M6.3 17.7l-2.1 2.1M19.8 4.2l-2.1 2.1" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-cloud">
          <path d="M6.8 18.4a4.4 4.4 0 0 1-.6-8.75 5.6 5.6 0 0 1 10.9-1.9 4.2 4.2 0 0 1-.5 10.65H6.8Z" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-rain">
          <path d="M6.8 13.4a4.4 4.4 0 0 1-.6-8.75 5.6 5.6 0 0 1 10.9-1.9 4.2 4.2 0 0 1-.5 10.65H6.8Z" />
          <path d="M8.4 17.4 7 21M12.6 17.4 11.2 21M16.8 17.4l-1.4 3.6" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-fog">
          <path d="M6.8 11.2a4.4 4.4 0 0 1-.4-8.75 5.6 5.6 0 0 1 10.7-2 4.2 4.2 0 0 1-.4 10.75Z" transform="translate(0 -1.5) scale(0.9)" />
          <path d="M3.4 14.6h17.2M3.4 18h17.2M3.4 21.4h17.2" />
        </symbol>
        <symbol viewBox="0 0 24 24" id="i-storm">
          <path d="M6.8 12.6a4.4 4.4 0 0 1-.5-8.75 5.6 5.6 0 0 1 10.8-1.9 4.2 4.2 0 0 1-.5 10.65H6.8Z" />
          <path d="M13 13.6l-3.4 5.2h3l-2 4.6 5.4-6.4h-3.2l2.4-3.4z" />
        </symbol>
      </defs>
    </svg>
  );
}

/** Sized by CSS via `.icon` (and whatever the calling context overrides), coloured by
 * `currentColor`. Decorative by default -- the adjacent text label is what a screen reader
 * should read, so the glyph stays `aria-hidden`. */
export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg className={className ? `icon ${className}` : "icon"} aria-hidden="true" focusable="false">
      <use href={`#i-${name}`} />
    </svg>
  );
}
