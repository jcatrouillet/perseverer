import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DateNavigator } from "./DateNavigator";

vi.mock("../api/queries", () => ({
  useActivityYears: () => ({ data: [2026, 2025], isLoading: false, isError: false }),
}));
vi.mock("../PersonalizeContext", () => ({
  usePersonalize: () => ({ week_start_day: "monday" }),
}));
vi.mock("../useIsMobile", () => ({ useIsMobile: () => false }));

describe("DateNavigator levels", () => {
  it("offers months from a year, then weeks and days from a month", () => {
    const { container, rerender } = render(<DateNavigator year={2026} />);
    expect(container.querySelectorAll(".date-nav__row--months a")).toHaveLength(12);
    expect(screen.getByRole("link", { name: "OCT" })).toHaveAttribute("href", "/calendar/2026/10");
    expect(container.querySelectorAll(".date-nav__week-row")).toHaveLength(0);

    rerender(<DateNavigator year={2026} month={10} />);
    expect(container.querySelectorAll(".date-nav__week-row")).toHaveLength(5);
    expect(screen.getByRole("link", { name: "View week of 2026-10-05" })).toHaveAttribute(
      "href",
      "/calendar/week/2026-10-05",
    );
    expect(container.querySelector('a[href="/day/2026-10-11"]')).toBeInTheDocument();
  });
});
