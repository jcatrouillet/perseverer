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

const GROUPED: ExplorerMetric[] = [
  { key: "weight", title: "Weight", content: <p>weight chart</p> },
  { key: "b:ldl", title: "LDL", group: "Blood · Lipids", content: <p>ldl chart</p> },
  { key: "b:hdl", title: "HDL", group: "Blood · Lipids", content: <p>hdl chart</p> },
  {
    key: "b:alt",
    title: "ALT",
    group: "Blood · Liver",
    hideDetailHeader: true,
    content: <p>alt chart</p>,
  },
];

describe("MetricExplorer groups", () => {
  it("lists ungrouped metrics flat and keeps other groups collapsed", () => {
    render(<MetricExplorer metrics={GROUPED} selected={null} onSelect={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Weight" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Blood · Lipids/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByRole("button", { name: "LDL" })).not.toBeInTheDocument();
  });

  it("opens a group by hand, showing its count, and selecting an entry calls onSelect", () => {
    const onSelect = vi.fn();
    render(<MetricExplorer metrics={GROUPED} selected={null} onSelect={onSelect} />);
    const toggle = screen.getByRole("button", { name: /Blood · Lipids/ });
    expect(toggle).toHaveTextContent("2");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("button", { name: "LDL" }));
    expect(onSelect).toHaveBeenCalledWith("b:ldl");
  });

  it("opens the group holding the selected metric automatically", () => {
    render(<MetricExplorer metrics={GROUPED} selected="b:alt" onSelect={vi.fn()} />);
    expect(screen.getByRole("button", { name: "ALT" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /Blood · Liver/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("skips the shared detail header for a metric that opts out", () => {
    const header = <div>window controls</div>;
    const { rerender } = render(
      <MetricExplorer metrics={GROUPED} selected="weight" onSelect={vi.fn()} detailHeader={header} />,
    );
    expect(screen.getByText("window controls")).toBeInTheDocument();
    rerender(
      <MetricExplorer metrics={GROUPED} selected="b:alt" onSelect={vi.fn()} detailHeader={header} />,
    );
    expect(screen.queryByText("window controls")).not.toBeInTheDocument();
    expect(screen.getByText("alt chart")).toBeInTheDocument();
  });
});

describe("MetricExplorer subgroups", () => {
  const NESTED: ExplorerMetric[] = [
    { key: "add", title: "Add results", group: "Blood tests", content: <p>add</p> },
    { key: "b:ldl", title: "LDL", group: "Blood tests", subgroup: "Lipids", content: <p>ldl</p> },
    { key: "b:alt", title: "ALT", group: "Blood tests", subgroup: "Liver", content: <p>alt</p> },
  ];

  it("nests subgroups under their group, each collapsed until opened", () => {
    render(<MetricExplorer metrics={NESTED} selected="add" onSelect={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Blood tests/ })).toHaveTextContent("3");
    expect(screen.getByRole("button", { name: "Add results" })).toBeInTheDocument();
    const lipids = screen.getByRole("button", { name: /Lipids/ });
    expect(lipids).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "LDL" })).not.toBeInTheDocument();
    fireEvent.click(lipids);
    expect(screen.getByRole("button", { name: "LDL" })).toBeInTheDocument();
  });

  it("opens the group and subgroup holding the selected metric", () => {
    render(<MetricExplorer metrics={NESTED} selected="b:alt" onSelect={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Liver/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /Lipids/ })).toHaveAttribute("aria-expanded", "false");
  });
});
