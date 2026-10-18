import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActivitySummary } from "../api/types";
import { CyclingStats } from "./CyclingStats";

// Recharts needs a real layout to draw; the charts' contents are covered by cyclingStats.test.ts.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div style={{ width: 400, height: 200 }}>{children}</div>
    ),
  };
});

function ride(id: string, date: string, km: number, minutes: number): ActivitySummary {
  return {
    id,
    local_date: date,
    start_time_utc: `${date}T08:00:00Z`,
    sport: "cycling",
    name: id,
    distance_m: km * 1000,
    moving_duration_s: minutes * 60,
    duration_s: minutes * 60,
    max_hr_bpm: 150,
  } as ActivitySummary;
}

describe("CyclingStats", () => {
  it("renders nothing for a period without rides", () => {
    const { container } = render(
      <CyclingStats
        rides={[]}
        allRides={[]}
        startDate="2026-01-01"
        endDate="2026-12-31"
        periodLabel="2026"
        trailingWindowDays={30}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the period's ride totals, records and a note when no ride has power", () => {
    const rides = [
      ride("short", "2026-03-02", 21, 60),
      ride("long", "2026-03-09", 41, 100),
      ride("fast", "2026-03-10", 20.5, 45),
    ];
    render(
      <CyclingStats
        rides={rides}
        allRides={rides}
        startDate="2026-01-01"
        endDate="2026-12-31"
        periodLabel="2026"
        trailingWindowDays={30}
      />,
    );
    expect(screen.getByRole("heading", { name: /Cycling/ })).toBeInTheDocument();
    expect(screen.getByText("Number of rides").closest(".stat-tile")).toHaveTextContent("3");
    expect(screen.getByText("Longest ride").closest(".stat-tile")).toHaveTextContent("41.0");
    expect(screen.getByText("Most often ridden on").closest(".stat-tile")).toHaveTextContent("Mon");
    expect(screen.getByText(/No ride in 2026 recorded power/)).toBeInTheDocument();
    // 20 km record: the faster of the two ~20 km rides; 40 km: the 41 km ride.
    const rows = screen.getAllByRole("row").map((r) => r.textContent);
    expect(rows.some((t) => t?.startsWith("20 km2026-03-10"))).toBe(true);
    expect(rows.some((t) => t?.startsWith("40 km2026-03-09"))).toBe(true);
  });
});
