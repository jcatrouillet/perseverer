import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TrendPoint } from "../trendWindow";
import { TrendChart, type TrendSeries } from "./TrendChart";

function point(x: string, values: Record<string, number | null>): TrendPoint {
  return { x, ts: new Date(`${x} 2026`).getTime() || 0, ...values };
}

describe("TrendChart", () => {
  it("renders nothing when no series has any data", () => {
    const series: TrendSeries[] = [{ key: "vo2max", label: "VO2max", color: "#000" }];
    const points = [point("Mon 1", { vo2max: null }), point("Tue 2", { vo2max: null })];
    const { container } = render(<TrendChart points={points} series={series} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a single-series line chart", () => {
    const series: TrendSeries[] = [{ key: "vo2max", label: "VO2max", color: "#000" }];
    const points = [point("Mon 1", { vo2max: 50 }), point("Tue 2", { vo2max: 52 })];
    const { container } = render(<TrendChart points={points} series={series} />);
    expect(container.querySelectorAll(".recharts-line")).toHaveLength(1);
  });

  it("omits the legend for a single series but shows it for two or more", () => {
    const oneSeries: TrendSeries[] = [{ key: "vo2max", label: "VO2max", color: "#000" }];
    const points = [point("Mon 1", { vo2max: 50 }), point("Tue 2", { vo2max: 52 })];
    const { container: single } = render(<TrendChart points={points} series={oneSeries} />);
    expect(single.querySelector(".chart-legend")).toBeNull();

    const twoSeries: TrendSeries[] = [
      { key: "systolic", label: "Systolic", color: "#111" },
      { key: "diastolic", label: "Diastolic", color: "#222" },
    ];
    const pairedPoints = [
      point("Mon 1", { systolic: 120, diastolic: 80 }),
      point("Tue 2", { systolic: 118, diastolic: 78 }),
    ];
    const { container: paired } = render(<TrendChart points={pairedPoints} series={twoSeries} />);
    expect(paired.querySelector(".chart-legend")).not.toBeNull();
    expect(screen.getByText("Systolic")).toBeInTheDocument();
    expect(screen.getByText("Diastolic")).toBeInTheDocument();
  });

  it("renders an area for series with kind 'area' and lines for the rest", () => {
    const series: TrendSeries[] = [
      { key: "ctl", label: "Fitness (CTL)", color: "#111", kind: "area" },
      { key: "atl", label: "Fatigue (ATL)", color: "#222" },
      { key: "tsb", label: "Form (TSB)", color: "#333", axis: "right" },
    ];
    const points = [
      point("Mon 1", { ctl: 40, atl: 35, tsb: 5 }),
      point("Tue 2", { ctl: 41, atl: 36, tsb: 5 }),
    ];
    const { container } = render(<TrendChart points={points} series={series} />);
    expect(container.querySelectorAll(".recharts-area")).toHaveLength(1);
    expect(container.querySelectorAll(".recharts-line")).toHaveLength(2);
  });

  it("shows a caption summarizing the latest bucket with real data, using each series' own formatter", () => {
    const series: TrendSeries[] = [
      {
        key: "weight_kg",
        label: "Weight",
        color: "#000",
        formatValue: (v) => `${v.toFixed(1)} kg`,
      },
    ];
    const points = [point("Mon 1", { weight_kg: 79.5 }), point("Tue 2", { weight_kg: null })];
    render(<TrendChart points={points} series={series} />);
    expect(screen.getByText(/Mon 1/)).toBeInTheDocument();
    expect(screen.getByText(/79\.5 kg/)).toBeInTheDocument();
  });

  it("renders a second Y axis only when a series asks for the right axis", () => {
    const leftOnly: TrendSeries[] = [{ key: "vo2max", label: "VO2max", color: "#000" }];
    const points = [point("Mon 1", { vo2max: 50 }), point("Tue 2", { vo2max: 52 })];
    const { container: single } = render(<TrendChart points={points} series={leftOnly} />);
    expect(single.querySelectorAll(".recharts-yAxis")).toHaveLength(1);

    const dualAxis: TrendSeries[] = [
      { key: "pace", label: "Pace", color: "#111" },
      { key: "hr", label: "HR", color: "#222", axis: "right" },
    ];
    const dualPoints = [
      point("Mon 1", { pace: 5, hr: 150 }),
      point("Tue 2", { pace: 5.1, hr: 148 }),
    ];
    const { container: dual } = render(<TrendChart points={dualPoints} series={dualAxis} />);
    expect(dual.querySelectorAll(".recharts-yAxis")).toHaveLength(2);
  });
});
