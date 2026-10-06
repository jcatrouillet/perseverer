import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  ActivityDetail,
  ClimbComparisonRowOut,
  ClimbComparisonsOut,
  SplitOut,
} from "../api/types";
import { ClimbComparisonTable } from "./ClimbComparisonTable";

function split(overrides: Partial<SplitOut> = {}): SplitOut {
  return {
    split_index: 0,
    split_type: "climb_active",
    start_time_utc: null,
    end_time_utc: null,
    duration_s: 60,
    distance_m: null,
    climb_grade: 2,
    climb_result: "completed",
    climb_avg_hr: null,
    climb_max_hr: null,
    is_manual: null,
    ...overrides,
  };
}

function activity(overrides: Partial<ActivityDetail> = {}): ActivityDetail {
  return {
    id: "current",
    start_time_utc: "2025-06-10T08:00:00Z",
    utc_offset_s: 0,
    local_date: "2025-06-10",
    sport: "rock_climbing",
    sub_sport: "bouldering",
    name: null,
    is_race: null,
    duration_s: 3600,
    moving_duration_s: 3600,
    distance_m: null,
    elevation_gain_m: null,
    max_altitude_m: null,
    calories: null,
    avg_hr_bpm: 100,
    max_hr_bpm: null,
    training_load: null,
    workout_rpe: null,
    weight_kg: null,
    vdot: null,
    workout_name: null,
    primary_source: "test",
    stream_available: false,
    climb_route_count: 1,
    climb_max_completed_grade: 2,
    climb_time_s: 60,
    device: null,
    laps: [],
    splits: [split()],
    route: null,
    metrics: [],
    estimated_sweat_loss_ml: null,
    carbohydrates_g: null,
    sodium_mg: null,
    transport_mix_flag: null,
    has_trim: false,
    duplicate_candidates: [],
    ...overrides,
  };
}

function row(overrides: Partial<ClimbComparisonRowOut> = {}): ClimbComparisonRowOut {
  return {
    id: "past1",
    local_date: "2025-06-01",
    duration_s: 3800,
    route_count: 12,
    max_completed_grade: 3,
    climb_time_s: 900,
    ...overrides,
  };
}

function comparisons(rows: ClimbComparisonRowOut[], matched_count?: number): ClimbComparisonsOut {
  return {
    duration_band_fraction: 0.15,
    matched_count: matched_count ?? rows.length,
    rows,
  };
}

describe("ClimbComparisonTable", () => {
  it("renders nothing when there are no comparison rows", () => {
    const { container } = render(
      <ClimbComparisonTable activity={activity()} comparisons={comparisons([])} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the current activity as its own row, marked as such, ahead of the history", () => {
    render(<ClimbComparisonTable activity={activity()} comparisons={comparisons([row()])} />);
    const rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("this session");
    expect(rows[2]).toHaveTextContent("2025-06-01");
  });

  it("computes the current session's own route count/max grade/climb time from its splits", () => {
    render(
      <ClimbComparisonTable
        activity={activity({
          splits: [
            split({ split_index: 0, climb_grade: 2, climb_result: "completed", duration_s: 60 }),
            split({ split_index: 2, climb_grade: 4, climb_result: "attempt", duration_s: 90 }),
          ],
        })}
        comparisons={comparisons([row()])}
      />,
    );
    const rows = screen.getAllByRole("row");
    // 2 routes, max *completed* grade V2 (not the V4 attempt), 150s total climb time.
    expect(rows[1]).toHaveTextContent("2");
    expect(rows[1]).toHaveTextContent("V2");
  });

  it("shows a dash for a session with no completed route rather than a fabricated grade", () => {
    render(
      <ClimbComparisonTable
        activity={activity()}
        comparisons={comparisons([row({ max_completed_grade: null, climb_time_s: null })])}
      />,
    );
    const rows = screen.getAllByRole("row");
    const historyRow = rows[2]!;
    const dashCount = (historyRow.textContent!.match(/—/g) ?? []).length;
    expect(dashCount).toBe(2);
  });

  it("links each history row to its own activity, but not the current row", () => {
    render(<ClimbComparisonTable activity={activity()} comparisons={comparisons([row()])} />);
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/activities/past1"]);
  });

  it("captions with the matched count and threshold when rows were capped", () => {
    render(<ClimbComparisonTable activity={activity()} comparisons={comparisons([row()], 14)} />);
    expect(screen.getByText(/1 of 14/)).toBeInTheDocument();
    expect(screen.getByText(/15%/)).toBeInTheDocument();
  });
});
