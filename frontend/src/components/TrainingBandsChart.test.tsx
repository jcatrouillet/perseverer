import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PaceBandOut } from "../api/types";
import { TrainingBandsChart } from "./TrainingBandsChart";

const mockUsePaceBands = vi.fn();

vi.mock("../api/queries", () => ({
  usePaceBands: () => mockUsePaceBands(),
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

function withData(overrides: Partial<Record<string, number>>): PaceBandOut[] {
  return ALL_ZERO.map((b) => ({ ...b, seconds: overrides[b.label] ?? 0 }));
}

describe("TrainingBandsChart", () => {
  it("shows a loading state while the fetch is in flight", () => {
    mockUsePaceBands.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    render(<TrainingBandsChart />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows an error message when the fetch fails", () => {
    mockUsePaceBands.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<TrainingBandsChart />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load training bands.");
  });

  it("renders nothing when every band is zero", () => {
    mockUsePaceBands.mockReturnValue({ data: ALL_ZERO, isLoading: false, isError: false });
    const { container } = render(<TrainingBandsChart />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the chart title once there is real data", () => {
    mockUsePaceBands.mockReturnValue({
      data: withData({ "5:00-5:30": 1800 }),
      isLoading: false,
      isError: false,
    });
    render(<TrainingBandsChart />);
    expect(screen.getByText("Training bands")).toBeInTheDocument();
  });

  it("defaults to Percentage and switches to Duration on click", () => {
    mockUsePaceBands.mockReturnValue({
      data: withData({ "5:00-5:30": 1800 }),
      isLoading: false,
      isError: false,
    });
    render(<TrainingBandsChart />);
    const percentageButton = screen.getByText("Percentage");
    const durationButton = screen.getByText("Duration");
    expect(percentageButton.className).toContain("is-active");
    expect(durationButton.className).not.toContain("is-active");

    fireEvent.click(durationButton);
    expect(durationButton.className).toContain("is-active");
    expect(percentageButton.className).not.toContain("is-active");
  });
});
