import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActivitySummary } from "../api/types";
import { WeekCyclingStats } from "./WeekCyclingStats";

// Recharts needs a real layout to draw; the series themselves are tested in cyclingStats.test.ts.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div style={{ width: 400, height: 200 }}>{children}</div>
    ),
  };
});

function ride(
  id: string,
  date: string,
  km: number,
  minutes: number,
  extra: Partial<ActivitySummary> = {},
): ActivitySummary {
  return {
    id,
    local_date: date,
    start_time_utc: `${date}T08:00:00Z`,
    sport: "cycling",
    name: id,
    distance_m: km * 1000,
    moving_duration_s: minutes * 60,
    duration_s: minutes * 60,
    calories: 400,
    elevation_gain_m: 120,
    ...extra,
  } as ActivitySummary;
}

const week = { weekStart: "2026-03-02", weekEnd: "2026-03-08" };

describe("WeekCyclingStats", () => {
  it("renders nothing for a week without rides", () => {
    const { container } = render(
      <WeekCyclingStats
        allRides={[ride("old", "2026-01-05", 20, 60)]}
        rangeStart="2025-03-09"
        {...week}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("totals the week's rides and flags an all-time record distance set this week", () => {
    const rides = [
      ride("a", "2026-03-03", 21, 60, { best_20min_power_w: 200, avg_power_w: 180 }),
      ride("b", "2026-03-06", 41, 100),
      ride("older", "2026-01-05", 20.5, 90), // slower 20 km ride earlier: this week's is the best
    ];
    render(<WeekCyclingStats allRides={rides} rangeStart="2025-03-09" {...week} />);
    expect(screen.getByRole("heading", { name: /Cycling/ })).toBeInTheDocument();
    expect(screen.getByText("Total distance").closest(".stat-tile")).toHaveTextContent("62.0");
    expect(screen.getByText("Total duration").closest(".stat-tile")).toHaveTextContent("2h 40m");
    expect(screen.getByText("Fastest speed").closest(".stat-tile")).toHaveTextContent("24.6");
    expect(screen.getByText("Calories burned").closest(".stat-tile")).toHaveTextContent("800");
    expect(screen.getByText("Total elevation").closest(".stat-tile")).toHaveTextContent("240");
    expect(screen.getByText(/all-time record.*set\s+this week/)).toHaveTextContent("20 km");
    expect(screen.getAllByText("Weekly best FTP").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Power vs distance").length).toBeGreaterThan(0);
  });
});
