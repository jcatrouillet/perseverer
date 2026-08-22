import { render, screen } from "@testing-library/react";
import { Router } from "wouter";
import { describe, expect, it } from "vitest";

import type { InsightOut } from "../api/types";
import { InsightCard } from "./InsightCard";

function insight(overrides: Partial<InsightOut> = {}): InsightOut {
  return {
    kind: "effort",
    window: "30d",
    title: "Longest distance (run)",
    detail: {},
    value_num: 10000,
    metric_key: null,
    sport_family: "run",
    activity_id: "a1",
    local_date: "2026-08-10",
    computed_at: "2026-08-14T00:00:00Z",
    ...overrides,
  };
}

describe("InsightCard", () => {
  it("renders the title and date", () => {
    render(
      <Router>
        <InsightCard insight={insight()} />
      </Router>,
    );
    expect(screen.getByText("Longest distance (run)")).toBeInTheDocument();
    expect(screen.getByText("2026-08-10")).toBeInTheDocument();
  });

  it("links to the subject activity when activity_id is present", () => {
    render(
      <Router>
        <InsightCard insight={insight({ activity_id: "abc123" })} />
      </Router>,
    );
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/activities/abc123");
  });

  it("renders without a link when there is no activity_id", () => {
    render(
      <Router>
        <InsightCard insight={insight({ activity_id: null })} />
      </Router>,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("falls back to a neutral style for an unrecognized kind", () => {
    const { container } = render(
      <Router>
        <InsightCard insight={insight({ kind: "mystery" })} />
      </Router>,
    );
    expect(container.querySelector(".tone-neutral")).toBeInTheDocument();
  });

  it("styles a window_best insight distinctly from an all-time pb", () => {
    const { container: windowBest } = render(
      <Router>
        <InsightCard insight={insight({ kind: "window_best", title: "Fastest 5 km in the last 30 days" })} />
      </Router>,
    );
    const { container: pb } = render(
      <Router>
        <InsightCard insight={insight({ kind: "pb", title: "All-time best 5 km" })} />
      </Router>,
    );
    expect(windowBest.querySelector(".tone-pace")).toBeInTheDocument();
    expect(pb.querySelector(".tone-pace")).toBeInTheDocument();
    // Different icons -- a window_best is a weaker claim than a genuine all-time PB, and
    // reusing the trophy icon would visually overstate it.
    expect(windowBest.querySelector("use")?.getAttribute("href")).not.toBe(
      pb.querySelector("use")?.getAttribute("href"),
    );
  });
});
