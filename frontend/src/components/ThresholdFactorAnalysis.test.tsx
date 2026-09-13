import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  ActivityRefOut,
  ThresholdFactorAnalysisOut,
  ThresholdHrBreakdownOut,
  ThresholdHrContributorOut,
  Vo2maxContributorOut,
  Vo2maxFactorAnalysisOut,
} from "../api/types";
import { ThresholdFactorAnalysis } from "./ThresholdFactorAnalysis";

const mockUseThresholdFactorAnalysis = vi.fn();

vi.mock("../api/queries", () => ({
  useThresholdFactorAnalysis: (...args: unknown[]) => mockUseThresholdFactorAnalysis(...args),
}));

function vo2maxContributor(overrides: Partial<Vo2maxContributorOut> = {}): Vo2maxContributorOut {
  return {
    activity_id: "race",
    local_date: "2026-08-14",
    name: "5k race",
    sport: "running",
    distance_m: 5000,
    duration_s: 1100,
    vdot: 50,
    ...overrides,
  };
}

function vo2max(overrides: Partial<Vo2maxFactorAnalysisOut> = {}): Vo2maxFactorAnalysisOut {
  return {
    as_of: "2026-09-13",
    window_start: "2026-08-03",
    window_end: "2026-09-13",
    rolling_vdot: 50,
    driving_activity: vo2maxContributor(),
    other_contributors: [],
    expires_on: "2026-09-25",
    days_since_last_qualifying_run: 2,
    missing: [],
    ...overrides,
  };
}

function hrContributor(
  overrides: Partial<ThresholdHrContributorOut> = {},
): ThresholdHrContributorOut {
  return {
    activity_id: "tempo",
    local_date: "2026-08-20",
    name: "Tempo run",
    sport: "running",
    distance_m: 8000,
    duration_s: 2000,
    pace_s_per_km: 255,
    avg_hr_bpm: 165,
    is_median: true,
    ...overrides,
  };
}

function activityRef(overrides: Partial<ActivityRefOut> = {}): ActivityRefOut {
  return {
    activity_id: "bike1",
    local_date: "2026-07-01",
    name: "Hard bike ride",
    sport: "cycling",
    distance_m: 40000,
    duration_s: 5400,
    ...overrides,
  };
}

function hrBreakdown(overrides: Partial<ThresholdHrBreakdownOut> = {}): ThresholdHrBreakdownOut {
  return {
    threshold_hr_bpm: 165,
    threshold_hr_source: "empirical",
    reference_pace_s_per_km: 255,
    contributors: [hrContributor()],
    max_hr_driving_activity: null,
    missing: [],
    ...overrides,
  };
}

function analysis(
  overrides: Partial<ThresholdFactorAnalysisOut> = {},
): ThresholdFactorAnalysisOut {
  return {
    as_of: "2026-09-13",
    vo2max: vo2max(),
    anaerobic_threshold_pace_s_per_km: 255,
    aerobic_threshold_pace_s_per_km: 297,
    anaerobic_threshold_hr: hrBreakdown(),
    aerobic_threshold_hr: hrBreakdown({
      reference_pace_s_per_km: 297,
      contributors: [hrContributor({ activity_id: "easy", name: "Easy run", avg_hr_bpm: 135 })],
    }),
    max_hr_bpm: 190,
    max_hr_source: "empirical",
    ...overrides,
  };
}

const EMPTY = { data: undefined, isLoading: false, isError: false };

describe("ThresholdFactorAnalysis", () => {
  it("shows a loading spinner while fetching", () => {
    mockUseThresholdFactorAnalysis.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<ThresholdFactorAnalysis />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows the VO2max driving workout as what sets both threshold paces", () => {
    mockUseThresholdFactorAnalysis.mockReturnValue({ ...EMPTY, data: analysis() });
    render(<ThresholdFactorAnalysis />);
    expect(screen.getByText("5k race")).toBeInTheDocument();
    expect(screen.getByText("Sets your threshold pace")).toBeInTheDocument();
  });

  it("shows both anaerobic and aerobic threshold HR sections with their own contributors", () => {
    mockUseThresholdFactorAnalysis.mockReturnValue({ ...EMPTY, data: analysis() });
    render(<ThresholdFactorAnalysis />);
    expect(screen.getByRole("heading", { name: "Anaerobic threshold HR" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Aerobic threshold HR" })).toBeInTheDocument();
    expect(screen.getByText("Tempo run")).toBeInTheDocument();
    expect(screen.getByText("Easy run")).toBeInTheDocument();
  });

  it("flags the median contributor and no others", () => {
    mockUseThresholdFactorAnalysis.mockReturnValue({
      ...EMPTY,
      data: analysis({
        anaerobic_threshold_hr: hrBreakdown({
          contributors: [
            hrContributor({ activity_id: "a", avg_hr_bpm: 150, is_median: false }),
            hrContributor({ activity_id: "b", avg_hr_bpm: 155, is_median: true }),
            hrContributor({ activity_id: "c", avg_hr_bpm: 160, is_median: false }),
          ],
        }),
        aerobic_threshold_hr: hrBreakdown({
          contributors: [hrContributor({ activity_id: "d", avg_hr_bpm: 135, is_median: false })],
        }),
      }),
    });
    render(<ThresholdFactorAnalysis />);
    expect(screen.getAllByText("Sets the median")).toHaveLength(1);
  });

  it("shows the max-HR driving activity when a threshold HR falls back to a fraction of it", () => {
    mockUseThresholdFactorAnalysis.mockReturnValue({
      ...EMPTY,
      data: analysis({
        anaerobic_threshold_hr: hrBreakdown({
          threshold_hr_source: "fallback",
          contributors: [],
          max_hr_driving_activity: activityRef(),
          missing: ["Only 1 qualifying run(s) near this pace..."],
        }),
      }),
    });
    render(<ThresholdFactorAnalysis />);
    expect(screen.getByText("Hard bike ride")).toBeInTheDocument();
    expect(screen.getByText("Set your max HR")).toBeInTheDocument();
    expect(screen.getByText(/Only 1 qualifying run/)).toBeInTheDocument();
  });

  it("shows the no-qualifying-run empty state when there's no VO2max driving activity", () => {
    mockUseThresholdFactorAnalysis.mockReturnValue({
      ...EMPTY,
      data: analysis({
        vo2max: vo2max({ driving_activity: null }),
        anaerobic_threshold_hr: hrBreakdown({
          threshold_hr_bpm: null,
          threshold_hr_source: null,
          reference_pace_s_per_km: null,
          contributors: [],
          missing: ["No threshold pace to measure runs against yet..."],
        }),
      }),
    });
    render(<ThresholdFactorAnalysis />);
    expect(screen.getByText("No qualifying run in the current window.")).toBeInTheDocument();
  });
});
