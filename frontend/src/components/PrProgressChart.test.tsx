import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ActivitySummary } from "../api/types";
import { PrProgressChart } from "./PrProgressChart";

const activities = [
  {
    id: "old",
    local_date: "2024-01-01",
    sport: "running",
    name: "Old 10K",
    vdot: 44,
    distance_m: 10000,
    moving_duration_s: 3000,
  },
  {
    id: "new",
    local_date: "2026-01-05",
    sport: "running",
    name: "Recent 8K",
    vdot: 46,
    distance_m: 8123,
    moving_duration_s: 2274.44,
  },
] as ActivitySummary[];

describe("PrProgressChart", () => {
  it("explains the empty state", () => {
    render(<PrProgressChart activities={[]} today="2026-09-20" />);
    expect(screen.getByText(/No running activities with a VDOT/)).toBeInTheDocument();
  });

  it("renders red and blue dots at exact distances with working activity links and hover details", () => {
    render(<PrProgressChart activities={activities} today="2026-09-20" />);
    const old = screen.getByRole("link", { name: /Old 10K, 2024/ });
    const recent = screen.getByRole("link", { name: /Recent 8K, 2026/ });
    expect(old).toHaveAttribute("href", "/activities/old");
    expect(recent).toHaveAttribute("href", "/activities/new");
    expect(old.querySelector("circle:last-child")).toHaveAttribute(
      "fill",
      "var(--color-heart-rate)",
    );
    expect(recent.querySelector("circle:last-child")).toHaveAttribute("fill", "var(--color-pace)");
    fireEvent.mouseEnter(recent);
    expect(screen.getByText(/8.12 km · 4:40 \/km · VDOT 46.0/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View run →" })).toHaveAttribute(
      "href",
      "/activities/new",
    );
    // Details persist while the pointer moves from the dot to the HTML activity link.
    fireEvent.mouseLeave(recent);
    expect(screen.getByRole("link", { name: "View run →" })).toBeInTheDocument();
    expect(old.isConnected).toBe(true);
    fireEvent.focus(old);
    expect(screen.getByRole("link", { name: "View run →" })).toHaveAttribute(
      "href",
      "/activities/old",
    );
  });
});
