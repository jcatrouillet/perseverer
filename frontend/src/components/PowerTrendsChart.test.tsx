import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActivitySummary, CyclingPowerOut } from "../api/types";
import { PowerTrendsChart } from "./PowerTrendsChart";

let mockPower: CyclingPowerOut | undefined;
vi.mock("../api/queries", () => ({
  useCyclingPower: () => ({ data: mockPower, isLoading: false, isError: false }),
}));
vi.mock("./SpeedTrendsChart", () => ({
  SpeedTrendsChart: () => <div>speed chart</div>,
}));
vi.mock("wouter", () => ({ useLocation: () => ["/", vi.fn()] }));

function ride(id: string, date: string, best20: number | null): ActivitySummary {
  return {
    id,
    local_date: date,
    sport: "cycling",
    name: id,
    best_20min_power_w: best20,
  } as ActivitySummary;
}

const history: CyclingPowerOut = {
  available: true,
  window_days: 90,
  current: null,
  history: [
    {
      as_of: "2025-01-10",
      ftp_w: 190,
      ftp_w_per_kg: 2.38,
      cp_w: null,
      w_prime_j: null,
      vo2max: null,
      weight_kg: 80,
      best_powers: [],
    },
  ],
  last_power_ride_date: "2025-01-10",
  missing: [],
};

describe("PowerTrendsChart", () => {
  it("opens on W/kg when rides have power and a weight, and can switch to speed", () => {
    mockPower = history;
    render(<PowerTrendsChart activities={[ride("r", "2025-01-10", 200)]} />);
    expect(screen.getByRole("heading", { name: "Power trends" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "W/kg" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Speed" }));
    expect(screen.getByText("speed chart")).toBeInTheDocument();
  });

  it("falls back to speed, and says why, when no ride has 20 minutes of power", () => {
    mockPower = { ...history, available: false, history: [] };
    render(<PowerTrendsChart activities={[ride("r", "2025-01-10", null)]} />);
    expect(screen.getByText("speed chart")).toBeInTheDocument();
    expect(screen.getByText(/No ride has 20 minutes of power yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "W" })).toBeDisabled();
  });
});
