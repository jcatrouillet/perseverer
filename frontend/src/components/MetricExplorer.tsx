// Metric list (left) + selected chart (right) -- shared by the Fitness & Form and Health tabs,
// per the user's own request to stop showing every metric's chart stacked at once and instead
// pick one from a list. Only the *list* is this component's job; the resolution/navigation
// controls above the chart and the chart itself are still the caller's own TrendControls/
// TrendChart, passed in as `content` for whichever metric is currently selected.
export interface ExplorerMetric {
  key: string;
  title: string;
  content: React.ReactNode;
}

export function MetricExplorer({
  metrics,
  selected,
  onSelect,
  detailHeader,
}: {
  metrics: ExplorerMetric[];
  /** The selected key, even if it doesn't match any current `metrics` entry (e.g. a metric that
   * had data on first load but not in this session) -- this component falls back to the first
   * available metric itself rather than asking every caller to pre-resolve that. */
  selected: string | null;
  onSelect: (key: string) => void;
  /** Rendered once above whichever metric's own `content` is currently shown -- the shared
   * resolution/navigation controls (TrendControls), which apply to every metric alike rather
   * than being part of any one metric's own content. */
  detailHeader?: React.ReactNode;
}) {
  if (metrics.length === 0) {
    return null;
  }
  const active = metrics.find((m) => m.key === selected) ?? metrics[0]!;

  return (
    <div className="metric-explorer">
      <nav className="metric-explorer__list" aria-label="Metrics">
        {metrics.map((m) => (
          <button
            key={m.key}
            type="button"
            className={
              m.key === active.key
                ? "metric-explorer__item metric-explorer__item--active"
                : "metric-explorer__item"
            }
            aria-current={m.key === active.key}
            onClick={() => onSelect(m.key)}
          >
            {m.title}
          </button>
        ))}
      </nav>
      <div className="metric-explorer__detail">
        {detailHeader}
        {active.content}
      </div>
    </div>
  );
}
