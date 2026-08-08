import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { FitnessDailyRollupOut } from "../api/types";
import { FitnessChart } from "./FitnessChart";

function point(
  local_date: string,
  training_load: number,
  ctl: number,
  atl: number,
  tsb: number,
): FitnessDailyRollupOut {
  return { local_date, training_load, ctl, atl, tsb };
}

describe("FitnessChart", () => {
  it("shows a placeholder message with fewer than two points", () => {
    render(<FitnessChart series={[point("2025-06-01", 100, 2.35, 13.31, 0)]} />);
    expect(screen.getByText(/not enough data/i)).toBeInTheDocument();
  });

  it("renders an empty-series placeholder", () => {
    render(<FitnessChart series={[]} />);
    expect(screen.getByText(/not enough data/i)).toBeInTheDocument();
  });

  it("renders all three series (CTL, ATL, TSB) for a real series", () => {
    const series = [
      point("2025-06-01", 100, 2.35, 13.31, 0),
      point("2025-06-02", 0, 2.3, 11.54, -10.96),
      point("2025-06-03", 150, 5.77, 29.97, -9.24),
    ];
    const { container } = render(<FitnessChart series={series} />);
    // CTL is an area (it's the baseline the other two are read against) and ATL/TSB are lines,
    // so this counts series rather than one specific Recharts element type.
    expect(container.querySelectorAll(".recharts-area")).toHaveLength(1);
    expect(container.querySelectorAll(".recharts-line")).toHaveLength(2);
  });

  it("marks only the most recent point on each series", () => {
    const series = [
      point("2025-06-01", 100, 2.35, 13.31, 0),
      point("2025-06-02", 0, 2.3, 11.54, -10.96),
      point("2025-06-03", 150, 5.77, 29.97, -9.24),
    ];
    const { container } = render(<FitnessChart series={series} />);
    // Three points x three series would be nine dots if every point were marked; the endpoint
    // treatment means exactly one per series.
    expect(container.querySelectorAll(".chart-endpoint")).toHaveLength(3);
  });

  it("summarizes the latest day's CTL/ATL/TSB values", () => {
    const series = [
      point("2025-06-01", 100, 2.35, 13.31, 0),
      point("2025-06-02", 0, 2.3, 11.54, -10.96),
    ];
    const { container } = render(<FitnessChart series={series} />);
    const paragraphs = Array.from(container.querySelectorAll("p"));
    const summary = paragraphs.find((p) => p.textContent?.includes("Latest"));
    expect(summary?.textContent).toMatch(/2025-06-02/);
    expect(summary?.textContent).toMatch(/2\.3/);
  });
});
