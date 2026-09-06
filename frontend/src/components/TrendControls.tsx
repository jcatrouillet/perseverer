// The one Week/Month/Year/All-time toggle + prev/next navigator shared by every chart on a
// tab (Fitness & Form, Health) -- a single control drives every metric chart on the page in
// sync, so "next week" moves VO2max, HRV, and everything else to the same new week at once,
// rather than each chart carrying its own independent (and much more cluttered) controls.
import type { Resolution, TrendWindow } from "../trendWindow";

const RESOLUTIONS: { value: Resolution; label: string }[] = [
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "year", label: "Year" },
  { value: "all", label: "All time" },
];

export function TrendControls({
  window,
  onResolutionChange,
  onPrevious,
  onNext,
}: {
  window: TrendWindow;
  onResolutionChange: (resolution: Resolution) => void;
  onPrevious: () => void;
  onNext: () => void;
}) {
  return (
    <div className="trend-controls">
      <div className="trend-controls__resolutions" role="group" aria-label="Chart resolution">
        {RESOLUTIONS.map((r) => (
          <button
            key={r.value}
            type="button"
            className={
              r.value === window.resolution
                ? "trend-controls__res trend-controls__res--active"
                : "trend-controls__res"
            }
            onClick={() => onResolutionChange(r.value)}
          >
            {r.label}
          </button>
        ))}
      </div>
      <div className="trend-controls__nav">
        {window.resolution !== "all" && (
          <button
            type="button"
            className="trend-controls__page"
            onClick={onPrevious}
            disabled={!window.canGoPrevious}
            aria-label="Earlier"
          >
            ‹
          </button>
        )}
        <span className="trend-controls__label">{window.label}</span>
        {window.resolution !== "all" && (
          <button
            type="button"
            className="trend-controls__page"
            onClick={onNext}
            disabled={!window.canGoNext}
            aria-label="Later"
          >
            ›
          </button>
        )}
      </div>
    </div>
  );
}
