import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChartFullscreen } from "./ChartFullscreen";

describe("ChartFullscreen", () => {
  it("renders its heading and children when there's a chart to show", () => {
    render(
      <ChartFullscreen title="Weight — over all time">
        <div data-testid="chart-body">chart</div>
      </ChartFullscreen>,
    );
    expect(screen.getAllByText("Weight — over all time").length).toBeGreaterThan(0);
    expect(screen.getByTestId("chart-body")).toBeInTheDocument();
  });

  it("renders nothing at all when children is null -- no dangling heading over an empty chart", () => {
    const { container } = render(
      <ChartFullscreen title="Weight — over all time">{null}</ChartFullscreen>,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryAllByText("Weight — over all time")).toHaveLength(0);
  });
});
