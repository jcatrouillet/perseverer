import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MetricExplorer, type ExplorerMetric } from "./MetricExplorer";

const METRICS: ExplorerMetric[] = [
  { key: "a", title: "Metric A", content: <p>Chart A</p> },
  { key: "b", title: "Metric B", content: <p>Chart B</p> },
];

describe("MetricExplorer", () => {
  it("renders nothing when there are no metrics", () => {
    const { container } = render(
      <MetricExplorer metrics={[]} selected={null} onSelect={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("falls back to the first metric when `selected` doesn't match any entry", () => {
    render(<MetricExplorer metrics={METRICS} selected="not-a-real-key" onSelect={vi.fn()} />);
    expect(screen.getByText("Chart A")).toBeInTheDocument();
    expect(screen.queryByText("Chart B")).not.toBeInTheDocument();
  });

  it("shows only the selected metric's own content, not every metric's", () => {
    render(<MetricExplorer metrics={METRICS} selected="b" onSelect={vi.fn()} />);
    expect(screen.getByText("Chart B")).toBeInTheDocument();
    expect(screen.queryByText("Chart A")).not.toBeInTheDocument();
  });

  it("marks the active item and calls onSelect when a different one is clicked", () => {
    const onSelect = vi.fn();
    render(<MetricExplorer metrics={METRICS} selected="a" onSelect={onSelect} />);
    expect(screen.getByRole("button", { name: "Metric A" })).toHaveClass(
      "metric-explorer__item--active",
    );
    fireEvent.click(screen.getByRole("button", { name: "Metric B" }));
    expect(onSelect).toHaveBeenCalledWith("b");
  });

  it("renders detailHeader once, above the active metric's content", () => {
    render(
      <MetricExplorer
        metrics={METRICS}
        selected="a"
        onSelect={vi.fn()}
        detailHeader={<div data-testid="header">Controls</div>}
      />,
    );
    expect(screen.getByTestId("header")).toBeInTheDocument();
    expect(screen.getByText("Chart A")).toBeInTheDocument();
  });
});
