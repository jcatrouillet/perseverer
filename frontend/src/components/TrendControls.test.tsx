import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { computeWindow, type CustomRange } from "../trendWindow";
import { TrendControls } from "./TrendControls";

const NO_CUSTOM_RANGE: CustomRange = { start: "2026-08-06", end: "2026-09-05" };

describe("TrendControls", () => {
  it("highlights the active resolution", () => {
    const window = computeWindow("week", "2026-08-27", "2020-01-01", "2026-09-05");
    render(
      <TrendControls
        window={window}
        onResolutionChange={vi.fn()}
        onPrevious={vi.fn()}
        onNext={vi.fn()}
        customRange={NO_CUSTOM_RANGE}
        onCustomRangeChange={vi.fn()}
        dataStart="2020-01-01"
        today="2026-09-05"
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
        customRange={NO_CUSTOM_RANGE}
        onCustomRangeChange={vi.fn()}
        dataStart="2020-01-01"
        today="2026-09-05"
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
        customRange={NO_CUSTOM_RANGE}
        onCustomRangeChange={vi.fn()}
        dataStart="2020-01-01"
        today="2026-09-05"
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
        customRange={NO_CUSTOM_RANGE}
        onCustomRangeChange={vi.fn()}
        dataStart="2020-01-01"
        today="2026-09-05"
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
        customRange={NO_CUSTOM_RANGE}
        onCustomRangeChange={vi.fn()}
        dataStart="2020-01-01"
        today="2026-09-05"
      />,
    );
    expect(screen.getByRole("button", { name: "Later" })).toBeDisabled();
  });

  it("shows two date inputs instead of prev/next/label when resolution is custom", () => {
    const range: CustomRange = { start: "2026-08-01", end: "2026-08-31" };
    const window = computeWindow("custom", "x", "2020-01-01", "2026-09-05", range);
    render(
      <TrendControls
        window={window}
        onResolutionChange={vi.fn()}
        onPrevious={vi.fn()}
        onNext={vi.fn()}
        customRange={range}
        onCustomRangeChange={vi.fn()}
        dataStart="2020-01-01"
        today="2026-09-05"
      />,
    );
    expect(screen.getByRole("button", { name: "Custom" })).toHaveClass(
      "trend-controls__res--active",
    );
    expect(screen.getByLabelText("Custom range start")).toHaveValue("2026-08-01");
    expect(screen.getByLabelText("Custom range end")).toHaveValue("2026-08-31");
    expect(screen.queryByRole("button", { name: "Earlier" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Later" })).not.toBeInTheDocument();
  });

  it("calls onCustomRangeChange when a custom range date input changes", () => {
    const range: CustomRange = { start: "2026-08-01", end: "2026-08-31" };
    const window = computeWindow("custom", "x", "2020-01-01", "2026-09-05", range);
    const onCustomRangeChange = vi.fn();
    render(
      <TrendControls
        window={window}
        onResolutionChange={vi.fn()}
        onPrevious={vi.fn()}
        onNext={vi.fn()}
        customRange={range}
        onCustomRangeChange={onCustomRangeChange}
        dataStart="2020-01-01"
        today="2026-09-05"
      />,
    );
    fireEvent.change(screen.getByLabelText("Custom range start"), {
      target: { value: "2026-08-10" },
    });
    expect(onCustomRangeChange).toHaveBeenCalledWith({ start: "2026-08-10", end: "2026-08-31" });

    fireEvent.change(screen.getByLabelText("Custom range end"), {
      target: { value: "2026-09-01" },
    });
    expect(onCustomRangeChange).toHaveBeenCalledWith({ start: "2026-08-01", end: "2026-09-01" });
  });
});
