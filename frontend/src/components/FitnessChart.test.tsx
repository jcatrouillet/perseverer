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

  it("renders three SVG paths (CTL, ATL, TSB) for a real series", () => {
    const series = [
      point("2025-06-01", 100, 2.35, 13.31, 0),
      point("2025-06-02", 0, 2.3, 11.54, -10.96),
      point("2025-06-03", 150, 5.77, 29.97, -9.24),
    ];
    const { container } = render(<FitnessChart series={series} />);
    const paths = container.querySelectorAll("path");
    expect(paths).toHaveLength(3);
    for (const path of paths) {
      // Three points -> "M x y L x y L x y"
      expect(path.getAttribute("d")).toMatch(/^M .+ L .+ L .+$/);
    }
  });

  it("summarizes the latest day's CTL/ATL/TSB values", () => {
    const series = [
      point("2025-06-01", 100, 2.35, 13.31, 0),
      point("2025-06-02", 0, 2.3, 11.54, -10.96),
    ];
    render(<FitnessChart series={series} />);
    expect(screen.getByText(/2025-06-02/)).toBeInTheDocument();
    expect(screen.getByText(/2\.3/)).toBeInTheDocument();
  });
});
