// Metric list (left) + selected chart (right) -- shared by the Fitness & Form and Health tabs,
// per the user's own request to stop showing every metric's chart stacked at once and instead
// pick one from a list. Only the *list* is this component's job; the resolution/navigation
// controls above the chart and the chart itself are still the caller's own TrendControls/
// TrendChart, passed in as `content` for whichever metric is currently selected.
//
// A metric may carry a `group`: those metrics are listed under a collapsible heading rather than
// flat, because the Health page's blood-test markers (dozens of them) would otherwise bury the
// handful of body metrics above them. A group is open while it holds the selected metric and can
// be toggled by hand; ungrouped metrics render flat, first, exactly as before.
import { useState } from "react";

export interface ExplorerMetric {
  key: string;
  title: string;
  content: React.ReactNode;
  /** Collapsible heading this metric is listed under. Omit for a flat, always-visible entry. */
  group?: string;
  /** Skip `detailHeader` for this metric -- for a metric whose own content isn't a windowed time
   * series (e.g. a blood marker's own all-time history, which the shared window controls above
   * every other metric's chart don't apply to). */
  hideDetailHeader?: boolean;
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
  // Explicit open/closed choices made by hand; a group with no entry here is open exactly when it
  // holds the selected metric.
  const [groupOverride, setGroupOverride] = useState<Record<string, boolean>>({});

  if (metrics.length === 0) {
    return null;
  }
  const active = metrics.find((m) => m.key === selected) ?? metrics[0]!;

  const flat = metrics.filter((m) => m.group === undefined);
  const groups: { name: string; items: ExplorerMetric[] }[] = [];
  for (const m of metrics) {
    if (m.group === undefined) continue;
    const existing = groups.find((g) => g.name === m.group);
    if (existing) existing.items.push(m);
    else groups.push({ name: m.group, items: [m] });
  }

  const isGroupOpen = (name: string) => groupOverride[name] ?? name === active.group;

  function renderItem(m: ExplorerMetric) {
    return (
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
    );
  }

  return (
    <div className="metric-explorer">
      <nav className="metric-explorer__list" aria-label="Metrics">
        {flat.map(renderItem)}
        {groups.map((g) => {
          const open = isGroupOpen(g.name);
          return (
            <div key={g.name} className="metric-explorer__group">
              <button
                type="button"
                className="metric-explorer__group-toggle"
                aria-expanded={open}
                onClick={() => setGroupOverride((prev) => ({ ...prev, [g.name]: !open }))}
              >
                <span className="metric-explorer__caret" aria-hidden="true">
                  {open ? "▾" : "▸"}
                </span>
                {g.name}
                <span className="metric-explorer__count">{g.items.length}</span>
              </button>
              {open && <div className="metric-explorer__group-items">{g.items.map(renderItem)}</div>}
            </div>
          );
        })}
      </nav>
      <div className="metric-explorer__detail">
        {!active.hideDetailHeader && detailHeader}
        {active.content}
      </div>
    </div>
  );
}
