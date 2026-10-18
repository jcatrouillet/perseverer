import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ClimbingSummaryOut } from "../api/types";
import { ClimbingStatsCard } from "./ClimbingStatsCard";

function summary(overrides: Partial<ClimbingSummaryOut> = {}): ClimbingSummaryOut {
  return {
    session_count: 3,
    total_climb_time_s: 5400,
    timed_session_count: 3,
    total_routes: 24,
    max_completed_grade: 4,
    grade_breakdown: [
      { grade: 2, attempted: 1, completed: 3 },
      { grade: 4, attempted: 3, completed: 1 },
    ],
    ...overrides,
  };
}

describe("ClimbingStatsCard", () => {
  it("renders nothing while the summary hasn't loaded yet", () => {
    const { container } = render(<ClimbingStatsCard summary={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when there were no bouldering sessions in the period", () => {
    const { container } = render(<ClimbingStatsCard summary={summary({ session_count: 0 })} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows session count, climb time, route count, and max grade", () => {
    render(<ClimbingStatsCard summary={summary()} />);
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Climb time")).toBeInTheDocument();
    expect(screen.getByText("1h 30m")).toBeInTheDocument();
    expect(screen.getByText("Routes")).toBeInTheDocument();
    expect(screen.getByText("24")).toBeInTheDocument();
    const maxGradeTile = screen.getByText("Max grade completed").closest(".stat-tile");
    expect(maxGradeTile).not.toBeNull();
    expect(within(maxGradeTile as HTMLElement).getByText("V4")).toBeInTheDocument();
  });

  it("omits the max-grade tile when nothing was completed", () => {
    render(<ClimbingStatsCard summary={summary({ max_completed_grade: null })} />);
    expect(screen.queryByText("Max grade completed")).not.toBeInTheDocument();
  });

  it("renders the grade-distribution chart", () => {
    render(<ClimbingStatsCard summary={summary()} />);
    expect(screen.getByText("Attempted")).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("shows an unknown climb time as a dash, never 0m, when no session was timed", () => {
    render(
      <ClimbingStatsCard
        summary={summary({ session_count: 1, total_climb_time_s: null, timed_session_count: 0 })}
      />,
    );
    const tile = screen.getByText("Climb time").closest(".stat-tile") as HTMLElement;
    expect(tile).toHaveTextContent("—");
    expect(tile).toHaveTextContent("not recorded (Kaya only)");
    expect(tile).not.toHaveTextContent("0m");
  });

  it("says how many sessions the climb time covers when only some were timed", () => {
    render(<ClimbingStatsCard summary={summary({ timed_session_count: 2 })} />);
    expect(screen.getByText("Climb time").closest(".stat-tile")).toHaveTextContent(
      "2 of 3 sessions timed",
    );
  });
});
