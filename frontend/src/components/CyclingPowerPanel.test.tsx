import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CyclingPowerOut, CyclingPowerPointOut } from "../api/types";
import { CyclingPowerPanel } from "./CyclingPowerPanel";

let mockData: CyclingPowerOut | undefined;
vi.mock("../api/queries", () => ({
  useCyclingPower: () => ({ data: mockData, isLoading: false, isError: false }),
}));

const point: CyclingPowerPointOut = {
  as_of: "2025-09-10",
  ftp_w: 156.8,
  ftp_w_per_kg: 2.0,
  cp_w: 157.3,
  w_prime_j: 12896,
  vo2max: 34.93,
  weight_kg: 78.4,
  best_powers: [
    { duration_s: 300, watts: 202, activity_id: "r1", local_date: "2025-09-10", name: "Zwift" },
    { duration_s: 1200, watts: 165, activity_id: "r1", local_date: "2025-09-10", name: "Zwift" },
  ],
};

describe("CyclingPowerPanel", () => {
  it("shows FTP, FTP/kg, Critical Power with W' and VO2max, and the rides behind them", () => {
    mockData = {
      available: true,
      window_days: 90,
      current: null,
      history: [point],
      last_power_ride_date: "2025-09-10",
      missing: ["No ride with power in the last 90 days (the last was 2025-09-10)."],
    };
    render(<CyclingPowerPanel />);
    expect(screen.getByText("FTP").closest(".stat-tile")).toHaveTextContent("157");
    expect(screen.getByText("FTP/kg").closest(".stat-tile")).toHaveTextContent("2.00");
    expect(screen.getByText("Critical Power").closest(".stat-tile")).toHaveTextContent(
      "W′ 12.9 kJ",
    );
    expect(screen.getByText("VO2max (estimate)").closest(".stat-tile")).toHaveTextContent("34.9");
    expect(screen.getByText(/90 days to 2025-09-10/)).toBeInTheDocument();
    // One row per duration; the same ride set both bests here.
    expect(screen.getAllByRole("link", { name: "Zwift · 2025-09-10" })).toHaveLength(2);
  });

  it("explains what is missing without any power ride", () => {
    mockData = {
      available: false,
      window_days: 90,
      current: null,
      history: [],
      last_power_ride_date: null,
      missing: ["No ride with power yet."],
    };
    render(<CyclingPowerPanel />);
    expect(screen.getByText("No ride with power yet.")).toBeInTheDocument();
    expect(screen.queryByText("FTP")).not.toBeInTheDocument();
  });
});
