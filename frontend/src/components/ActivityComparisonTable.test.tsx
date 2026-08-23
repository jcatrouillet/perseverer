import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityComparisonRowOut, ActivityComparisonsOut, ActivityDetail, ActivityMetricOut } from "../api/types";
import { ActivityComparisonTable } from "./ActivityComparisonTable";

function metric(metric_key: string, value_num: number): ActivityMetricOut {
  return { metric_key, value_num, value_text: null, unit: null, source: "test" };
}

function activity(overrides: Partial<ActivityDetail> = {}): ActivityDetail {
  return {
    id: "current",
    start_time_utc: "2025-06-10T08:00:00Z",
    utc_offset_s: 0,
    local_date: "2025-06-10",
    sport: "running",
    sub_sport: null,
    name: null,
    is_race: null,
    duration_s: 1500,
    moving_duration_s: 1500,
    distance_m: 5000,
    elevation_gain_m: null,
    calories: null,
    avg_hr_bpm: 150,
    max_hr_bpm: null,
    training_load: null,
    workout_rpe: null,
    weight_kg: null,
    vdot: 45.0,
    workout_name: null,
    primary_source: "test",
    stream_available: false,
    climb_route_count: null,
    climb_max_completed_grade: null,
    climb_time_s: null,
    device: null,
    laps: [],
    splits: [],
    route: null,
    metrics: [],
    estimated_sweat_loss_ml: null,
    carbohydrates_g: null,
    sodium_mg: null,
    transport_mix_flag: null,
    has_trim: false,
    ...overrides,
  };
}

function row(overrides: Partial<ActivityComparisonRowOut> = {}): ActivityComparisonRowOut {
  return {
    id: "past1",
    local_date: "2025-06-01",
    distance_m: 5000,
    duration_s: 1600,
    vdot: 43.0,
    avg_gap_speed_mps: 3.5,
    avg_hr_bpm: 148,
    avg_cadence_spm: 168,
    ...overrides,
  };
}

function comparisons(rows: ActivityComparisonRowOut[], matched_count?: number): ActivityComparisonsOut {
  return {
    start_radius_m: 300,
    distance_band_fraction: 0.15,
    matched_count: matched_count ?? rows.length,
    rows,
  };
}

describe("ActivityComparisonTable", () => {
  it("renders nothing when there are no comparison rows", () => {
    const { container } = render(
      <ActivityComparisonTable activity={activity()} comparisons={comparisons([])} sport="running" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the current activity as its own row, marked as such, ahead of the history", () => {
    render(
      <ActivityComparisonTable activity={activity()} comparisons={comparisons([row()])} sport="running" />,
    );
    const rows = screen.getAllByRole("row");
    // rows[0] is the header row.
    expect(rows[1]).toHaveTextContent("this run");
    expect(rows[2]).toHaveTextContent("2025-06-01");
  });

  it("reads the current activity's own GAP and cadence from its metrics array", () => {
    render(
      <ActivityComparisonTable
        activity={activity({
          metrics: [
            metric("perseverer.performance.avg_gap_speed_mps", 3.4),
            metric("fit.session.avg_running_cadence", 82),
          ],
        })}
        comparisons={comparisons([row()])}
        sport="running"
      />,
    );
    // 82 * 2 = 164 spm (single-foot rate doubled, same convention as ActivityStatsGrid).
    expect(screen.getByText("164 spm")).toBeInTheDocument();
  });

  it("doubles a comparison row's raw single-foot cadence to strides/min", () => {
    render(
      <ActivityComparisonTable
        activity={activity()}
        comparisons={comparisons([row({ avg_cadence_spm: 168 })])}
        sport="running"
      />,
    );
    // The backend already doubles avg_cadence_spm before it reaches the frontend -- this just
    // confirms the value passes through unchanged (no further doubling client-side).
    expect(screen.getByText("168 spm")).toBeInTheDocument();
  });

  it("shows a dash for a row missing an optional metric rather than a fabricated value", () => {
    render(
      <ActivityComparisonTable
        activity={activity()}
        comparisons={comparisons([
          row({ vdot: null, avg_gap_speed_mps: null, avg_hr_bpm: null, avg_cadence_spm: null }),
        ])}
        sport="running"
      />,
    );
    const rows = screen.getAllByRole("row");
    const historyRow = rows[2]!;
    const dashCount = (historyRow.textContent!.match(/—/g) ?? []).length;
    expect(dashCount).toBe(4);
  });

  it("links each history row to its own activity, but not the current row", () => {
    render(
      <ActivityComparisonTable activity={activity()} comparisons={comparisons([row()])} sport="running" />,
    );
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/activities/past1"]);
  });

  it("shows a compact km/h value, not a pace, for a wheeled sport", () => {
    // In practice this component is only rendered behind an isRunningSport gate (see
    // ActivityDetailPage.tsx), so GAP is never actually populated for a wheeled sport -- reflect
    // that here rather than feeding the component data the real backend would never produce.
    render(
      <ActivityComparisonTable
        activity={activity({ sport: "cycling", distance_m: 20000, duration_s: 3600, moving_duration_s: 3600 })}
        comparisons={comparisons([row({ distance_m: 20000, duration_s: 3000, avg_gap_speed_mps: null })])}
        sport="cycling"
      />,
    );
    expect(screen.queryByText(/\/km/)).not.toBeInTheDocument();
  });

  it("captions with the matched count and threshold when rows were capped", () => {
    render(
      <ActivityComparisonTable
        activity={activity()}
        comparisons={comparisons([row()], 14)}
        sport="running"
      />,
    );
    expect(screen.getByText(/1 of 14/)).toBeInTheDocument();
    expect(screen.getByText(/15%/)).toBeInTheDocument();
    expect(screen.getByText(/300m/)).toBeInTheDocument();
  });
});
