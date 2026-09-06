import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { computeWindow } from "../trendWindow";
import { TrendControls } from "./TrendControls";

describe("TrendControls", () => {
  it("highlights the active resolution", () => {
    const window = computeWindow("week", "2026-08-27", "2020-01-01", "2026-09-05");
    render(
      <TrendControls
        window={window}
        onResolutionChange={vi.fn()}
        onPrevious={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Week" })).toHaveClass("trend-controls__res--active");
    expect(screen.getByRole("button", { name: "Month" })).not.toHaveClass(
      "trend-controls__res--active",
    );
  });

  it("calls onResolutionChange when a different resolution is clicked", () => {
    const onResolutionChange = vi.fn();
    const window = computeWindow("week", "2026-08-27", "2020-01-01", "2026-09-05");
    render(
      <TrendControls
        window={window}
        onResolutionChange={onResolutionChange}
        onPrevious={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Year" }));
    expect(onResolutionChange).toHaveBeenCalledWith("year");
  });

  it("shows the window's own label and wires prev/next", () => {
    const onPrevious = vi.fn();
    const onNext = vi.fn();
    const window = computeWindow("month", "2026-08-15", "2020-01-01", "2026-09-05");
    render(
      <TrendControls
        window={window}
        onResolutionChange={vi.fn()}
        onPrevious={onPrevious}
        onNext={onNext}
      />,
    );
    expect(screen.getByText("August 2026")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Earlier" }));
    expect(onPrevious).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    expect(onNext).toHaveBeenCalled();
  });

  it("hides prev/next for all-time and disables the boundary a window can't move past", () => {
    const allTime = computeWindow("all", "x", "2020-01-01", "2026-09-05");
    const { unmount } = render(
      <TrendControls
        window={allTime}
        onResolutionChange={vi.fn()}
        onPrevious={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Earlier" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Later" })).not.toBeInTheDocument();
    unmount();

    const atToday = computeWindow("week", "2026-09-05", "2020-01-01", "2026-09-05");
    render(
      <TrendControls
        window={atToday}
        onResolutionChange={vi.fn()}
        onPrevious={vi.fn()}
        onNext={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Later" })).toBeDisabled();
  });
});
