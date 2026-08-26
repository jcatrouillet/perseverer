// A running-figure loading indicator -- swaps the app's old plain "Loading…" text for something
// that reads as "in motion" everywhere data is in flight.
//
// Not Icon's `run` glyph (Phosphor's PersonSimpleRun, used for sport tagging elsewhere): that's
// one static filled pose, and animating it as a single rigid unit (a bounce/lean) never actually
// reads as "running" -- the limbs don't move. This is a small hand-rolled stick figure instead,
// built as separate arm/leg line segments that each pivot at the shoulder/hip via CSS `transform:
// rotate()`, opposite-arm-opposite-leg paired (as in a real gait) and a slight torso bob at twice
// the stride frequency (a runner rises on every footstrike, twice per full leg-swing cycle).
// Deliberately plain single-segment limbs, not a jointed thigh/shin chain -- this is this
// project's one hand-drawn figure (Icon.tsx's own docstring notes that a detailed hand-authored
// running *silhouette* repeatedly failed user feedback), so it stays a minimal scissor-running
// pictogram rather than trying to be anatomically detailed. Stroke-only on a 24x24 grid, paints
// with currentColor -- the same convention every other hand-rolled glyph in Icon.tsx already
// uses. `role="status"` plus the visible text label (not just an aria-hidden figure) is what
// actually announces "loading" to a screen reader -- the animation itself is decorative.
import "../styles/loading-spinner.css";

export function LoadingSpinner({
  label = "Loading…",
  size = "md",
}: {
  label?: string;
  size?: "sm" | "md" | "lg";
}) {
  return (
    <span className={`loading-spinner loading-spinner--${size}`} role="status">
      <svg
        className="loading-spinner__icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        aria-hidden="true"
        focusable="false"
      >
        <g className="loading-spinner__bob">
          <circle cx="12.8" cy="4" r="2" />
          <line x1="12.5" y1="6" x2="10.8" y2="13" />
          <g className="loading-spinner__arm-a">
            <line x1="12.3" y1="6.8" x2="12.3" y2="11" />
          </g>
          <g className="loading-spinner__arm-b">
            <line x1="12.3" y1="6.8" x2="12.3" y2="11" />
          </g>
          <g className="loading-spinner__leg-a">
            <line x1="10.8" y1="13" x2="10.8" y2="19" />
          </g>
          <g className="loading-spinner__leg-b">
            <line x1="10.8" y1="13" x2="10.8" y2="19" />
          </g>
        </g>
      </svg>
      <span className="loading-spinner__label">{label}</span>
    </span>
  );
}
