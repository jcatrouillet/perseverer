import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DuplicateCandidateOut, TrimCandidateOut } from "../api/types";
import { DuplicateSideLink, TrimCandidateRow } from "./SettingsPage";

function trimCandidate(overrides: Partial<TrimCandidateOut> = {}): TrimCandidateOut {
  return {
    id: "act1",
    name: "Santa Cruz County Hiking",
    sport: "hiking",
    start_time_utc: "2024-03-01T10:00:00Z",
    distance_m: 51500.0,
    duration_s: 5520.0,
    flag: {
      at_start: false,
      at_end: true,
      suggested_trim_start_s: null,
      suggested_trim_end_s: 1680.0,
    },
    ...overrides,
  };
}

function duplicateCandidate(
  overrides: Partial<DuplicateCandidateOut> = {},
): DuplicateCandidateOut {
  return {
    id: "other1",
    name: "Half Dome",
    primary_source: "strava_export",
    start_time_utc: "2022-06-07T12:48:32Z",
    distance_m: 26863.1,
    duration_s: 41913.0,
    ...overrides,
  };
}

describe("TrimCandidateRow", () => {
  it("links to the activity and names the flagged boundary", () => {
    render(<TrimCandidateRow candidate={trimCandidate()} />);
    const link = screen.getByRole("link", { name: /Santa Cruz County Hiking/ });
    expect(link).toHaveAttribute("href", "/activities/act1");
    expect(screen.getByText(/Fast segment at the end/)).toBeInTheDocument();
  });

  it("names both boundaries when both are flagged", () => {
    render(
      <TrimCandidateRow
        candidate={trimCandidate({ flag: { ...trimCandidate().flag, at_start: true } })}
      />,
    );
    expect(screen.getByText(/Fast segment at the start and end/)).toBeInTheDocument();
  });
});

describe("DuplicateSideLink", () => {
  it("links to the activity with its source in parentheses", () => {
    render(<DuplicateSideLink activity={duplicateCandidate()} />);
    const link = screen.getByRole("link", { name: /Half Dome \(Strava export\)/ });
    expect(link).toHaveAttribute("href", "/activities/other1");
  });
});
