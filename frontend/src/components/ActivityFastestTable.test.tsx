import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityContextRecentOut } from "../api/types";
import { ActivityFastestTable } from "./ActivityFastestTable";

function row(
  id: string,
  local_date: string,
  distance_m: number,
  duration_s: number,
  avg_hr_bpm?: number,
): ActivityContextRecentOut {
  return { id, local_date, distance_m, duration_s, avg_hr_bpm: avg_hr_bpm ?? null };
}

describe("ActivityFastestTable", () => {
  it("renders nothing with fewer than two comparable rows", () => {
    const { container } = render(
      <ActivityFastestTable
        fastest={[row("a1", "2025-06-01", 5000, 1500)]}
        sport="running"
        currentActivityId="a1"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists rows in the order the API already sorted them (fastest first), each linking to its activity", () => {
    render(
      <ActivityFastestTable
        fastest={[
          row("fast", "2025-06-02", 5000, 1200),
          row("target", "2025-06-01", 5000, 1500),
          row("slow", "2025-06-03", 5000, 1800),
        ]}
        sport="running"
        currentActivityId="target"
      />,
    );
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/activities/fast",
      "/activities/target",
      "/activities/slow",
    ]);
  });

  it("highlights the current activity's own row", () => {
    render(
      <ActivityFastestTable
        fastest={[row("fast", "2025-06-02", 5000, 1200), row("target", "2025-06-01", 5000, 1500)]}
        sport="running"
        currentActivityId="target"
      />,
    );
    const targetLink = screen.getByRole("link", { name: /2025-06-01/ });
    expect(targetLink.className).toContain("activity-fastest__row--current");
  });

  it("shows a compact km/h value, not a pace, for a wheeled sport", () => {
    render(
      <ActivityFastestTable
        fastest={[row("a1", "2025-06-01", 20000, 3600), row("a2", "2025-06-02", 20000, 3000)]}
        sport="cycling"
        currentActivityId="a1"
      />,
    );
    expect(screen.getByText("20.0km/h")).toBeInTheDocument();
    expect(screen.queryByText(/\/km/)).not.toBeInTheDocument();
  });

  it("shows avg HR when present, and omits it when absent", () => {
    render(
      <ActivityFastestTable
        fastest={[
          row("with-hr", "2025-06-01", 5000, 1500, 142),
          row("no-hr", "2025-06-02", 5000, 1600),
        ]}
        sport="running"
        currentActivityId="with-hr"
      />,
    );
    expect(screen.getByText("142bpm")).toBeInTheDocument();
    expect(screen.queryByText("bpm")).not.toBeInTheDocument();
  });

  it("titles the section using the current activity's own floored distance", () => {
    render(
      <ActivityFastestTable
        fastest={[row("a1", "2025-06-01", 10230, 3400), row("target", "2025-06-02", 10230, 3300)]}
        sport="running"
        currentActivityId="target"
      />,
    );
    expect(screen.getByText("Fastest 10 km runs")).toBeInTheDocument();
  });

  it("floors rather than rounds the distance, matching the backend's own km-bucket", () => {
    // 9.76km must read "9 km" (the [9000, 10000) bucket it's actually compared within), not
    // "10 km" -- Math.round would say 10 here, which is a different bucket entirely.
    render(
      <ActivityFastestTable
        fastest={[row("a1", "2025-06-01", 9764, 3400), row("target", "2025-06-02", 9764, 3300)]}
        sport="running"
        currentActivityId="target"
      />,
    );
    expect(screen.getByText("Fastest 9 km runs")).toBeInTheDocument();
  });
});
