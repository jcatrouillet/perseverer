import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TooltipContentProps } from "recharts";

import { GoalTooltip, type ChartPoint } from "./GoalProgressChart";

// Driving GoalTooltip through a real Recharts mouse-hover would need a real ResizeObserver/
// layout engine jsdom doesn't provide (see GoalProgressChart.tsx's own comment on why
// GoalTooltip is exported) -- render it directly instead, with a fabricated `payload` matching
// the one real shape isTooltipRow actually reads (`{ payload: ChartPoint }`).
function actualPoint(overrides: Partial<ChartPoint> = {}): ChartPoint {
  return {
    ts: Date.parse("2026-08-17T00:00:00Z"),
    km: 100,
    seriesLabel: "Actual",
    targetKm: 90,
    diffKm: 10,
    addedKm: 5,
    isToday: false,
    ...overrides,
  };
}

// GoalTooltip only ever reads `active`/`payload`/`unitLabel` (see isTooltipRow), but
// TooltipContentProps itself requires several other Recharts-internal fields it never touches --
// filled in with harmless placeholders so these fixtures satisfy the type without lying about
// what the component actually reads.
function tooltipProps(
  active: boolean,
  point: ChartPoint,
): TooltipContentProps & { unitLabel: string } {
  return {
    active,
    payload: [{ payload: point }],
    unitLabel: "km",
    coordinate: undefined,
    accessibilityLayer: false,
    activeIndex: undefined,
  } as unknown as TooltipContentProps & { unitLabel: string };
}

describe("GoalTooltip", () => {
  it("renders nothing when inactive", () => {
    const { container } = render(<GoalTooltip {...tooltipProps(false, actualPoint())} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the target/current/ahead breakdown for a historical (non-today) point", () => {
    render(
      <GoalTooltip {...tooltipProps(true, actualPoint({ targetKm: 90, km: 100, diffKm: 10 }))} />,
    );
    expect(screen.getByText(/Target: 90\.0 km/)).toBeInTheDocument();
    expect(screen.getByText(/Current: 100\.0 km/)).toBeInTheDocument();
    expect(screen.getByText(/Ahead: \+10\.0 km/)).toBeInTheDocument();
  });

  it("shows a negative difference as Behind, not a negatively-signed Ahead", () => {
    render(
      <GoalTooltip
        {...tooltipProps(true, actualPoint({ targetKm: 1101.1, km: 1018.6, diffKm: -82.5 }))}
      />,
    );
    expect(screen.getByText(/Behind: -82\.5 km/)).toBeInTheDocument();
    expect(screen.queryByText(/Ahead/)).not.toBeInTheDocument();
  });

  it("adds the 'ran today' sentence only for today's own point", () => {
    render(
      <GoalTooltip
        {...tooltipProps(
          true,
          actualPoint({ isToday: true, addedKm: 4.6, diffKm: 142.4, targetKm: 1413.5, km: 1555.9 }),
        )}
      />,
    );
    expect(
      screen.getByText(
        /The 4\.6 km you ran today puts you 142\.4 km ahead of your goal for today\./,
      ),
    ).toBeInTheDocument();
  });

  it("omits the 'ran today' sentence for a historical point even with the same shape", () => {
    render(
      <GoalTooltip
        {...tooltipProps(true, actualPoint({ isToday: false, addedKm: 4.6, diffKm: 142.4 }))}
      />,
    );
    expect(screen.queryByText(/you ran today/)).not.toBeInTheDocument();
  });

  it("phrases today's sentence as 'behind' when the day's diff is negative", () => {
    render(
      <GoalTooltip
        {...tooltipProps(true, actualPoint({ isToday: true, addedKm: 2.0, diffKm: -12.3 }))}
      />,
    );
    expect(screen.getByText(/behind your goal for today\./)).toBeInTheDocument();
  });

  it("renders nothing when the hovered payload has no Actual row (e.g. only the Target line)", () => {
    const targetOnly: ChartPoint = {
      ts: Date.parse("2026-01-01T00:00:00Z"),
      km: 0,
      seriesLabel: "Target",
    };
    const { container } = render(<GoalTooltip {...tooltipProps(true, targetOnly)} />);
    expect(container.firstChild).toBeNull();
  });
});
