import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PaceVariabilityResult } from "../paceVariability";
import { PaceVariabilityChart } from "./PaceVariabilityChart";

function result(overrides: Partial<PaceVariabilityResult> = {}): PaceVariabilityResult {
  return {
    variabilityPct: 4.8,
    segments: [
      { paceMinPerKm: 5.0, distanceM: 100, relativeDeviation: 0.2 },
      { paceMinPerKm: 5.5, distanceM: 100, relativeDeviation: 1.0 },
      { paceMinPerKm: 5.1, distanceM: 100, relativeDeviation: 0.1 },
    ],
    ...overrides,
  };
}

describe("PaceVariabilityChart", () => {
  it("shows the rounded percentage in the centre", () => {
    render(<PaceVariabilityChart result={result()} />);
    expect(screen.getByText("5%")).toBeInTheDocument();
  });

  it("renders one bar per segment", () => {
    const { container } = render(<PaceVariabilityChart result={result()} />);
    expect(container.querySelectorAll(".pace-variability__bar")).toHaveLength(3);
  });

  it("exposes the percentage via an accessible label", () => {
    render(<PaceVariabilityChart result={result({ variabilityPct: 12.4 })} />);
    expect(screen.getByRole("img", { name: "Pace variability: 12 percent" })).toBeInTheDocument();
  });

  it("labels each bar with its real cumulative distance range, not a split index", () => {
    const { container } = render(<PaceVariabilityChart result={result()} />);
    const titles = Array.from(container.querySelectorAll(".pace-variability__bar title")).map(
      (t) => t.textContent,
    );
    expect(titles).toEqual([
      "0.00–0.10 km: 5:00 /km",
      "0.10–0.20 km: 5:30 /km",
      "0.20–0.30 km: 5:06 /km",
    ]);
  });
});
