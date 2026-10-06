import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Vo2maxContributorOut, Vo2maxFactorAnalysisOut } from "../api/types";
import { Vo2maxFactorAnalysis } from "./Vo2maxFactorAnalysis";

const mockUseVo2maxFactorAnalysis = vi.fn();

vi.mock("../api/queries", () => ({
  useVo2maxFactorAnalysis: (...args: unknown[]) => mockUseVo2maxFactorAnalysis(...args),
}));

function contributor(overrides: Partial<Vo2maxContributorOut> = {}): Vo2maxContributorOut {
  return {
    activity_id: "act1",
    local_date: "2026-08-20",
    name: "5k race",
    sport: "running",
    distance_m: 5000,
    duration_s: 1100,
    vdot: 52.3,
    ...overrides,
  };
}

function analysis(overrides: Partial<Vo2maxFactorAnalysisOut> = {}): Vo2maxFactorAnalysisOut {
  return {
    as_of: "2026-09-13",
    window_start: "2026-08-02",
    window_end: "2026-09-13",
    rolling_vdot: 52.3,
    driving_activity: contributor(),
    other_contributors: [],
    expires_on: "2026-10-01",
    days_since_last_qualifying_run: 5,
    missing: [],
    ...overrides,
  };
}

const EMPTY = { data: undefined, isLoading: false, isError: false };

describe("Vo2maxFactorAnalysis", () => {
  it("shows a loading spinner while fetching", () => {
    mockUseVo2maxFactorAnalysis.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<Vo2maxFactorAnalysis />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows the driving activity marked as current best, and its window bounds", () => {
    mockUseVo2maxFactorAnalysis.mockReturnValue({ ...EMPTY, data: analysis() });
    render(<Vo2maxFactorAnalysis />);
    expect(screen.getByText("5k race")).toBeInTheDocument();
    expect(screen.getByText("Current best")).toBeInTheDocument();
    expect(screen.getByText(/2026-08-02 to 2026-09-13/)).toBeInTheDocument();
  });

  it("lists other contributors separately from the driving activity", () => {
    mockUseVo2maxFactorAnalysis.mockReturnValue({
      ...EMPTY,
      data: analysis({
        other_contributors: [contributor({ activity_id: "act2", name: "Tempo run", vdot: 48.0 })],
      }),
    });
    render(<Vo2maxFactorAnalysis />);
    expect(screen.getByText("Tempo run")).toBeInTheDocument();
    // Only the driving activity gets the badge.
    expect(screen.getAllByText("Current best")).toHaveLength(1);
  });

  it("shows an empty state when there's no qualifying run at all", () => {
    mockUseVo2maxFactorAnalysis.mockReturnValue({
      ...EMPTY,
      data: analysis({ driving_activity: null, rolling_vdot: null, expires_on: null }),
    });
    render(<Vo2maxFactorAnalysis />);
    expect(screen.getByText("No qualifying run in the current window.")).toBeInTheDocument();
  });

  it("renders each missing/gap message", () => {
    mockUseVo2maxFactorAnalysis.mockReturnValue({
      ...EMPTY,
      data: analysis({
        missing: ["Your last qualifying run was 30 days ago — a fresh hard effort..."],
      }),
    });
    render(<Vo2maxFactorAnalysis />);
    expect(screen.getByText(/30 days ago/)).toBeInTheDocument();
  });
});
