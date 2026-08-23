import { fireEvent, render, screen } from "@testing-library/react";
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
    mockUsePaceBandsByActivity.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    });
    render(<TrainingBandsChart />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows an error message when either fetch fails", () => {
    mockUsePaceBands.mockReturnValue({ data: undefined, isLoading: false, isError: false });
    mockUsePaceBandsByActivity.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    render(<TrainingBandsChart />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load training bands.");
  });

  it("renders nothing when both the aggregate and the per-run data are empty", () => {
    mockBoth({ aggregate: ALL_ZERO, byActivity: [] });
    const { container } = render(<TrainingBandsChart />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the aggregate chart even with no per-run data, and no composition/duration charts", () => {
    mockBoth({ aggregate: aggregateWithData({ "5:00-5:30": 1800 }), byActivity: [] });
    render(<TrainingBandsChart />);
    // getByRole, not getByText: ChartFullscreen's mobile tap-to-expand affordance renders the
    // title twice (a real button plus an aria-hidden static span, CSS-toggled) -- getByRole's
    // accessible-name computation correctly excludes the aria-hidden copy.
    expect(screen.getByRole("heading", { name: "Training bands" })).toBeInTheDocument();
    expect(screen.getByText(/Total time spent at each pace/)).toBeInTheDocument();
    expect(screen.queryByText(/Every run as its own bar/)).not.toBeInTheDocument();
    expect(
      screen.queryByText("Duration of each run -- always the full history."),
    ).not.toBeInTheDocument();
  });

  it("renders the composition and duration charts even with no aggregate data", () => {
    mockBoth({ aggregate: ALL_ZERO, byActivity: ONE_RUN });
    render(<TrainingBandsChart />);
    expect(screen.getByRole("heading", { name: "Training bands" })).toBeInTheDocument();
    expect(screen.getByText(/Every run as its own bar/)).toBeInTheDocument();
    expect(
      screen.getByText("Duration of each run -- always the full history."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Total time spent at each pace/)).not.toBeInTheDocument();
  });

  it("renders all three charts when both sources have data", () => {
    mockBoth({ aggregate: aggregateWithData({ "5:00-5:30": 1800 }), byActivity: ONE_RUN });
    render(<TrainingBandsChart />);
    expect(screen.getByText(/Every run as its own bar/)).toBeInTheDocument();
    expect(screen.getByText(/Total time spent at each pace/)).toBeInTheDocument();
    expect(
      screen.getByText("Duration of each run -- always the full history."),
    ).toBeInTheDocument();
  });

  it("uses the same full-bleed width treatment as Pace trends", () => {
    mockBoth({ aggregate: aggregateWithData({ "5:00-5:30": 1800 }), byActivity: ONE_RUN });
    const { container } = render(<TrainingBandsChart />);
    expect(container.querySelector("section")?.className).toContain("training-bands--wide");
  });

  it("clicking a pace in the middle chart isolates it in the top chart, and the reset button clears it", () => {
    mockBoth({ aggregate: aggregateWithData({ "5:00-5:30": 1800 }), byActivity: ONE_RUN });
    const { container } = render(<TrainingBandsChart />);

    expect(
      screen.getByText(/Click a pace in the chart below to isolate it here/),
    ).toBeInTheDocument();

    // Three charts render top to bottom (composition, aggregate, duration) -- the aggregate
    // chart's own wrapper is the second one. Recharts doesn't render a rectangle at all for a
    // zero-height bar, and this fixture only gives "5:00-5:30" a nonzero value, so exactly one
    // rectangle exists here -- unambiguously that band's own.
    const aggregateWrapper = container.querySelectorAll(".recharts-wrapper")[1]!;
    const bars = aggregateWrapper.querySelectorAll(".recharts-rectangle");
    expect(bars).toHaveLength(1);
    fireEvent.click(bars[0]!);

    expect(screen.getByText(/Showing only 5:00-5:30 \/km/)).toBeInTheDocument();
    expect(screen.queryByText(/Click a pace in the chart below/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Show every pace"));

    expect(
      screen.getByText(/Click a pace in the chart below to isolate it here/),
    ).toBeInTheDocument();
  });
});
