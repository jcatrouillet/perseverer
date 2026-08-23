import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SplitOut } from "../api/types";
import { BoulderingRoutesTable } from "./BoulderingRoutesTable";

function split(overrides: Partial<SplitOut> = {}): SplitOut {
  return {
    split_index: 0,
    split_type: "climb_active",
    start_time_utc: "2026-08-19T01:46:47Z",
    end_time_utc: "2026-08-19T01:47:38Z",
    duration_s: 51.6,
    distance_m: null,
    climb_grade: 2,
    climb_result: "completed",
    climb_avg_hr: 99,
    climb_max_hr: 132,
    ...overrides,
  };
}

describe("BoulderingRoutesTable", () => {
  it("renders nothing for an activity with no climb splits", () => {
    const { container } = render(<BoulderingRoutesTable splits={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for a non-bouldering activity's ordinary splits", () => {
    const { container } = render(
      <BoulderingRoutesTable
        splits={[split({ split_type: "interval_active", climb_grade: null, climb_result: null })]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists one row per route, in order, with grade/status/HR", () => {
    render(
      <BoulderingRoutesTable
        splits={[
          split({ split_index: 0, climb_grade: 2, climb_result: "completed" }),
          split({ split_index: 1, split_type: "climb_rest", climb_grade: null, climb_result: null }),
          split({ split_index: 2, climb_grade: 4, climb_result: "attempt", climb_avg_hr: 150 }),
        ]}
      />,
    );
    expect(screen.getByText("V2")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("V4")).toBeInTheDocument();
    expect(screen.getByText("Attempt")).toBeInTheDocument();
    expect(screen.getByText("150 bpm")).toBeInTheDocument();
  });

  it("captions with the completed/total count", () => {
    render(
      <BoulderingRoutesTable
        splits={[
          split({ split_index: 0, climb_result: "completed" }),
          split({ split_index: 2, climb_result: "attempt" }),
          split({ split_index: 4, climb_result: "attempt" }),
        ]}
      />,
    );
    expect(screen.getByText("1 of 3 routes completed.")).toBeInTheDocument();
  });

  it("shows a dash rather than a fabricated value when HR is missing", () => {
    render(<BoulderingRoutesTable splits={[split({ climb_avg_hr: null })]} />);
    const rows = screen.getAllByRole("row");
    expect(rows[1]!).toHaveTextContent("—");
  });
});
