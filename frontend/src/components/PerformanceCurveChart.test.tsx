import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ActivitySummary, PerformanceCurveOut, PerformanceCurvePointOut } from "../api/types";
import { PerformanceCurveChart } from "./PerformanceCurveChart";

const mockUseAllActivities = vi.fn();
const mockUsePerformanceCurve = vi.fn();

vi.mock("../api/queries", () => ({
  useAllActivities: (...args: unknown[]) => mockUseAllActivities(...args),
  usePerformanceCurve: (...args: unknown[]) => mockUsePerformanceCurve(...args),
}));

const EMPTY = { data: undefined, isLoading: false, isError: false };

function activity(overrides: Partial<ActivitySummary> = {}): ActivitySummary {
  return {
    id: "a1",
    start_time_utc: "2026-06-01T10:00:00Z",
    utc_offset_s: 0,
    local_date: "2026-06-01",
    sport: "running",
    sub_sport: null,
    name: null,
    is_race: null,
    duration_s: 1800,
    moving_duration_s: 1800,
    distance_m: 5000,
    elevation_gain_m: null,
    max_altitude_m: null,
    calories: null,
    avg_hr_bpm: null,
    max_hr_bpm: null,
    training_load: null,
    workout_rpe: null,
    weight_kg: null,
    ...overrides,
  } as ActivitySummary;
}

function point(duration_s: number, value: number): PerformanceCurvePointOut {
  return { duration_s, value, activity_id: "a1", local_date: "2026-06-01" };
}

const UNAVAILABLE: PerformanceCurveOut = {
  available: false,
  metric: "pace",
  points: [],
  threshold_pace_s_per_km: null,
  aerobic_threshold_pace_s_per_km: null,
  threshold_hr_bpm: null,
  aerobic_threshold_hr_bpm: null,
  max_hr_bpm: null,
};

// 60-min best (320 s/km) is slower than the threshold pace (310 s/km).
const PACE_AVAILABLE: PerformanceCurveOut = {
  available: true,
  metric: "pace",
  points: [point(1200, 300), point(3600, 320)],
  threshold_pace_s_per_km: 310,
  aerobic_threshold_pace_s_per_km: 340,
  threshold_hr_bpm: null,
  aerobic_threshold_hr_bpm: null,
  max_hr_bpm: null,
};

// 60-min best (145 bpm) is lower than the threshold HR (148 bpm).
const HR_AVAILABLE: PerformanceCurveOut = {
  available: true,
  metric: "heart_rate",
  points: [point(1200, 150), point(3600, 145)],
  threshold_pace_s_per_km: null,
  aerobic_threshold_pace_s_per_km: null,
  threshold_hr_bpm: 148,
  aerobic_threshold_hr_bpm: 135,
  max_hr_bpm: 185,
};

describe("PerformanceCurveChart", () => {
  beforeEach(() => {
    mockUseAllActivities.mockReturnValue({
      ...EMPTY,
      data: [activity({ id: "a1", sport: "running" }), activity({ id: "a2", sport: "cycling" })],
    });
  });

  it("shows a loading spinner while fetching", () => {
    mockUsePerformanceCurve.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<PerformanceCurveChart />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows a message when nothing qualifies in range", () => {
    mockUsePerformanceCurve.mockReturnValue({ ...EMPTY, data: UNAVAILABLE });
    render(<PerformanceCurveChart />);
    expect(screen.getByText(/No qualifying activity/)).toBeInTheDocument();
  });

  it("defaults to Pace and re-fetches with the newly selected metric on switch", () => {
    mockUsePerformanceCurve.mockReturnValue({ ...EMPTY, data: PACE_AVAILABLE });
    render(<PerformanceCurveChart />);
    expect(mockUsePerformanceCurve.mock.calls[0]?.[0]).toBe("pace");

    fireEvent.click(screen.getByRole("tab", { name: "Heart rate" }));
    expect(mockUsePerformanceCurve.mock.calls.at(-1)?.[0]).toBe("heart_rate");
  });

  it("shows sport checkboxes only for heart rate, default all checked", () => {
    mockUsePerformanceCurve.mockReturnValue({ ...EMPTY, data: PACE_AVAILABLE });
    render(<PerformanceCurveChart />);
    expect(screen.queryByRole("group", { name: "Sports included" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Heart rate" }));
    const group = screen.getByRole("group", { name: "Sports included" });
    expect(within(group).getByLabelText("running")).toBeChecked();
    expect(within(group).getByLabelText("cycling")).toBeChecked();
  });

  it("unchecking a sport removes it from the effective selection", () => {
    mockUsePerformanceCurve.mockReturnValue({ ...EMPTY, data: HR_AVAILABLE });
    render(<PerformanceCurveChart />);
    fireEvent.click(screen.getByRole("tab", { name: "Heart rate" }));

    const group = screen.getByRole("group", { name: "Sports included" });
    const cycling = within(group).getByLabelText("cycling");
    fireEvent.click(cycling);
    expect(cycling).not.toBeChecked();

    const lastSportsArg = mockUsePerformanceCurve.mock.calls.at(-1)?.[3] as string[];
    expect(lastSportsArg).toContain("running");
    expect(lastSportsArg).not.toContain("cycling");
  });

  it("renders one reference line per non-null threshold value for pace", () => {
    mockUsePerformanceCurve.mockReturnValue({ ...EMPTY, data: PACE_AVAILABLE });
    const { container } = render(<PerformanceCurveChart />);
    expect(container.querySelectorAll(".recharts-reference-line").length).toBe(2);
  });

  it("omits reference lines when threshold values are null", () => {
    mockUsePerformanceCurve.mockReturnValue({
      ...EMPTY,
      data: { ...PACE_AVAILABLE, threshold_pace_s_per_km: null, aerobic_threshold_pace_s_per_km: null },
    });
    const { container } = render(<PerformanceCurveChart />);
    expect(container.querySelectorAll(".recharts-reference-line").length).toBe(0);
  });

  it("shows a faster/slower comparison sentence for pace", () => {
    mockUsePerformanceCurve.mockReturnValue({ ...EMPTY, data: PACE_AVAILABLE });
    render(<PerformanceCurveChart />);
    expect(screen.getByText(/slower than your computed threshold pace/)).toBeInTheDocument();
  });

  it("shows a higher/lower comparison sentence for heart rate", () => {
    mockUsePerformanceCurve.mockReturnValue({ ...EMPTY, data: HR_AVAILABLE });
    render(<PerformanceCurveChart />);
    fireEvent.click(screen.getByRole("tab", { name: "Heart rate" }));
    expect(screen.getByText(/lower than your computed threshold heart rate/)).toBeInTheDocument();
  });

  it("omits the comparison sentence when the 60-min bucket has no data", () => {
    mockUsePerformanceCurve.mockReturnValue({
      ...EMPTY,
      data: { ...PACE_AVAILABLE, points: [point(1200, 300)] },
    });
    render(<PerformanceCurveChart />);
    expect(screen.queryByText(/your computed threshold/)).not.toBeInTheDocument();
  });
});
