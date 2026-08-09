import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityContextOut, ActivityContextRecentOut } from "../api/types";
import { ActivityContextStrip } from "./ActivityContextStrip";

function recent(id: string, local_date: string, distance_m: number, duration_s: number): ActivityContextRecentOut {
  return { id, local_date, distance_m, duration_s };
}

function context(overrides: Partial<ActivityContextOut> = {}): ActivityContextOut {
  return { percentile_rank: null, comparable_count: 0, recent: [], ...overrides };
}

describe("ActivityContextStrip", () => {
  it("renders nothing when there's no percentile and fewer than two recent points", () => {
    const { container } = render(
      <ActivityContextStrip
        context={context({ recent: [recent("a1", "2025-06-01", 5000, 1500)] })}
        sport="running"
        currentActivityId="a1"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the percentile headline with the real comparable count", () => {
    render(
      <ActivityContextStrip
        context={context({ percentile_rank: 80, comparable_count: 5 })}
        sport="running"
        currentActivityId="a1"
      />,
    );
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByText(/5 similar running efforts/)).toBeInTheDocument();
  });

  it("uses singular 'effort' for exactly one comparable activity", () => {
    render(
      <ActivityContextStrip
        context={context({ percentile_rank: 100, comparable_count: 1 })}
        sport="running"
        currentActivityId="a1"
      />,
    );
    expect(screen.getByText(/1 similar running effort within 15% distance\./)).toBeInTheDocument();
  });

  it("omits the headline entirely when percentile_rank is null (nothing to compare against)", () => {
    render(
      <ActivityContextStrip
        context={context({
          percentile_rank: null,
          recent: [
            recent("a1", "2025-06-01", 5000, 1500),
            recent("a2", "2025-06-02", 5200, 1560),
          ],
        })}
        sport="running"
        currentActivityId="a1"
      />,
    );
    expect(screen.queryByText(/similar/)).not.toBeInTheDocument();
  });

  it("renders a sparkline point per recent activity with real distance/duration", () => {
    const { container } = render(
      <ActivityContextStrip
        context={context({
          recent: [
            recent("a1", "2025-06-01", 5000, 1500),
            recent("a2", "2025-06-02", 5200, 1560),
            recent("a3", "2025-06-03", 4800, 1440),
          ],
        })}
        sport="running"
        currentActivityId="a2"
      />,
    );
    expect(container.querySelectorAll(".recharts-scatter-symbol")).toHaveLength(3);
  });

  it("excludes a zero-distance or zero-duration recent entry from the sparkline", () => {
    const { container } = render(
      <ActivityContextStrip
        context={context({
          recent: [
            recent("a1", "2025-06-01", 5000, 1500),
            recent("a2", "2025-06-02", 5200, 1560),
            recent("strength", "2025-06-03", 0, 1800),
          ],
        })}
        sport="running"
        currentActivityId="a1"
      />,
    );
    expect(container.querySelectorAll(".recharts-scatter-symbol")).toHaveLength(2);
  });
});
