import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActivityPaceBandsOut, PaceBandOut } from "../api/types";
import { TrainingBandsChart } from "./TrainingBandsChart";

const mockUsePaceBands = vi.fn();
const mockUsePaceBandsByActivity = vi.fn();

vi.mock("../api/queries", () => ({
  usePaceBands: () => mockUsePaceBands(),
  usePaceBandsByActivity: () => mockUsePaceBandsByActivity(),
}));

const ALL_ZERO: PaceBandOut[] = [
  { label: "< 3:30", seconds: 0 },
  { label: "3:30-4:00", seconds: 0 },
  { label: "4:00-4:30", seconds: 0 },
  { label: "4:30-5:00", seconds: 0 },
  { label: "5:00-5:30", seconds: 0 },
  { label: "5:30-6:00", seconds: 0 },
  { label: "6:00-6:30", seconds: 0 },
  { label: "6:30-7:00", seconds: 0 },
  { label: "7:00-7:30", seconds: 0 },
  { label: "7:30-8:00", seconds: 0 },
  { label: "8:00-8:30", seconds: 0 },
  { label: "Walk", seconds: 0 },
];

function aggregateWithData(overrides: Partial<Record<string, number>>): PaceBandOut[] {
  return ALL_ZERO.map((b) => ({ ...b, seconds: overrides[b.label] ?? 0 }));
}

const ONE_RUN: ActivityPaceBandsOut[] = [
  {
    activity_id: "a1",
    local_date: "2026-01-15",
    bands: aggregateWithData({ "5:00-5:30": 1200, Walk: 300 }),
  },
];

function mockBoth(opts: {
  aggregate?: PaceBandOut[];
  byActivity?: ActivityPaceBandsOut[];
  isLoading?: boolean;
  isError?: boolean;
}) {
  const { aggregate = ALL_ZERO, byActivity = [], isLoading = false, isError = false } = opts;
  mockUsePaceBands.mockReturnValue({ data: aggregate, isLoading, isError });
  mockUsePaceBandsByActivity.mockReturnValue({ data: byActivity, isLoading, isError });
}

describe("TrainingBandsChart", () => {
  it("shows a loading state while either fetch is in flight", () => {
    mockUsePaceBands.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    mockUsePaceBandsByActivity.mockReturnValue({ data: undefined, isLoading: false, isError: false });
    render(<TrainingBandsChart />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows an error message when either fetch fails", () => {
    mockUsePaceBands.mockReturnValue({ data: undefined, isLoading: false, isError: false });
    mockUsePaceBandsByActivity.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<TrainingBandsChart />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load training bands.");
  });

  it("renders nothing when both the aggregate and the per-run data are empty", () => {
    mockBoth({ aggregate: ALL_ZERO, byActivity: [] });
    const { container } = render(<TrainingBandsChart />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the aggregate chart even with no per-run data", () => {
    mockBoth({ aggregate: aggregateWithData({ "5:00-5:30": 1800 }), byActivity: [] });
    render(<TrainingBandsChart />);
    expect(screen.getByText("Training bands")).toBeInTheDocument();
    expect(
      screen.getByText("Total time spent at each pace, summed across the whole running history."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Every run as its own bar/)).not.toBeInTheDocument();
  });

  it("renders the composition chart even with no aggregate data", () => {
    mockBoth({ aggregate: ALL_ZERO, byActivity: ONE_RUN });
    render(<TrainingBandsChart />);
    expect(screen.getByText("Training bands")).toBeInTheDocument();
    expect(screen.getByText(/Every run as its own bar/)).toBeInTheDocument();
    expect(screen.queryByText(/Total time spent at each pace/)).not.toBeInTheDocument();
  });

  it("renders both charts when both have data", () => {
    mockBoth({ aggregate: aggregateWithData({ "5:00-5:30": 1800 }), byActivity: ONE_RUN });
    render(<TrainingBandsChart />);
    expect(screen.getByText(/Every run as its own bar/)).toBeInTheDocument();
    expect(screen.getByText(/Total time spent at each pace/)).toBeInTheDocument();
  });
});
