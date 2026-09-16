import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { computeEddingtonBars } from "../eddington";
import { EddingtonBarChart } from "./EddingtonBarChart";

describe("EddingtonBarChart", () => {
  it("renders nothing for an empty bar list", () => {
    const { container } = render(<EddingtonBarChart bars={[]} unitLabel="km" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a bar per km threshold", () => {
    const bars = computeEddingtonBars([10, 8, 8]);
    const { container } = render(<EddingtonBarChart bars={bars} unitLabel="km" />);
    expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(bars.length);
  });
});
