import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ClimbGradeBreakdownOut } from "../api/types";
import { ClimbGradeChart } from "./ClimbGradeChart";

describe("ClimbGradeChart", () => {
  it("renders nothing for an empty breakdown", () => {
    const { container } = render(<ClimbGradeChart gradeBreakdown={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the Attempted/Completed legend for real data", () => {
    const breakdown: ClimbGradeBreakdownOut[] = [
      { grade: 2, attempted: 1, completed: 3 },
      { grade: 4, attempted: 3, completed: 1 },
    ];
    render(<ClimbGradeChart gradeBreakdown={breakdown} />);
    expect(screen.getByText("Attempted")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });
});
