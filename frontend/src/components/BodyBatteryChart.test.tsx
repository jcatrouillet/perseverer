import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BodyBatteryChart, type BodyBatteryPoint } from "./BodyBatteryChart";

function point(hour: number, level: number): BodyBatteryPoint {
  return { timestamp: `2026-08-22T${String(hour).padStart(2, "0")}:00:00Z`, level };
}

describe("BodyBatteryChart", () => {
  it("renders nothing when there are no points", () => {
    const { container } = render(<BodyBatteryChart points={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a chart when points are present", () => {
    const { container } = render(
      <BodyBatteryChart points={[point(7, 19), point(15, 82), point(23, 38)]} />,
    );
    expect(container.querySelector(".recharts-responsive-container")).toBeInTheDocument();
  });
});
