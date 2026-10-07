// A small pure RGB-lerp helper for the route map's pace-colour gradient (red = faster, blue =
// slower, per docs/ARCHITECTURE.md). The two endpoint colours are resolved from this app's existing
// `--color-heart-rate`/`--color-pace` theme tokens at render time (see ActivityRouteMap.tsx),
// not hardcoded here -- this module only does the interpolation math, so no new hex value
// enters the codebase (the design doc's "a hue always identifies a metric" rule).
export type Rgb = [number, number, number];

/** Parses a `#rrggbb` or `#rgb` string (what `getComputedStyle` resolves CSS colour custom
 * properties to) into an [r, g, b] triple. Returns a mid-grey fallback for anything else rather
 * than throwing, since this only ever runs against resolved theme tokens. */
export function hexToRgb(hex: string): Rgb {
  const clean = hex.trim().replace(/^#/, "");
  const full =
    clean.length === 3
      ? clean
          .split("")
          .map((c) => c + c)
          .join("")
      : clean;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return [128, 128, 128];
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

/** Linear interpolation between two RGB colours, `t` clamped to [0, 1], as a CSS `rgb()` string
 * Leaflet's `pathOptions.color` accepts directly. */
export function lerpColor(from: Rgb, to: Rgb, t: number): string {
  const clamped = Math.max(0, Math.min(1, t));
  const r = Math.round(from[0] + (to[0] - from[0]) * clamped);
  const g = Math.round(from[1] + (to[1] - from[1]) * clamped);
  const b = Math.round(from[2] + (to[2] - from[2]) * clamped);
  return `rgb(${r}, ${g}, ${b})`;
}
