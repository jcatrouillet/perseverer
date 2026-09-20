import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActivityRefOut, PaceHrZoneOut, PaceHrZonesOut, ZoneRunSampleOut } from "../api/types";
import { PaceHrZonesTable } from "./PaceHrZonesTable";

const mockUsePaceHrZones = vi.fn();

vi.mock("../api/queries", () => ({
  usePaceHrZones: (...args: unknown[]) => mockUsePaceHrZones(...args),
}));

function activityRef(overrides: Partial<ActivityRefOut> = {}): ActivityRefOut {
  return {
    activity_id: "act1",
    local_date: "2026-01-15",
    name: "Tempo run",
    sport: "running",
    distance_m: 10000,
    duration_s: 2500,
    ...overrides,
  };
}

function sampleRun(overrides: Partial<ZoneRunSampleOut> = {}): ZoneRunSampleOut {
  return { ...activityRef(), pace_s_per_km: 300, avg_hr_bpm: 150, ...overrides };
}

function zone(overrides: Partial<PaceHrZoneOut> = {}): PaceHrZoneOut {
  return {
    number: 1,
    label: "Recovery",
    description: "Very easy effort for warm-ups and cool-downs.",
    // Zone 1 is only bounded on its fast side (the pace it transitions to zone 2 at) -- unbounded
    // slower, matching what pace_hr_zones.py actually produces for the open lower zone.
    pace_fast_s_per_km: 420,
    pace_slow_s_per_km: null,
    hr_low_bpm: null,
    hr_high_bpm: 140,
    hr_source: "empirical",
    qualifying_run_count: 5,
    sample_runs: [sampleRun()],
    ...overrides,
  };
}

function zones(overrides: Partial<PaceHrZonesOut> = {}): PaceHrZonesOut {
  return {
    as_of: "2026-09-19",
    profile_vdot: 50.0,
    profile_vdot_activity: activityRef({ activity_id: "best", local_date: "2025-06-01" }),
    profile_max_hr_bpm: 190,
    profile_max_hr_source: "empirical",
    zones: [
      zone({ number: 1, label: "Recovery", qualifying_run_count: 20 }),
      zone({
        number: 2,
        label: "Basic Endurance",
        pace_fast_s_per_km: 360,
        pace_slow_s_per_km: 420,
        hr_low_bpm: 140,
        hr_high_bpm: 155,
      }),
      zone({
        number: 3,
        label: "Aerobic Threshold",
        pace_fast_s_per_km: 320,
        pace_slow_s_per_km: 360,
        hr_low_bpm: 155,
        hr_high_bpm: 163,
      }),
      zone({
        number: 4,
        label: "Lactate Threshold",
        pace_fast_s_per_km: 280,
        pace_slow_s_per_km: 320,
        hr_low_bpm: 163,
        hr_high_bpm: 178,
        hr_source: "formula_fallback",
        qualifying_run_count: 1,
      }),
      zone({
        number: 5,
        label: "VO2 Max",
        pace_fast_s_per_km: null,
        pace_slow_s_per_km: 280,
        hr_low_bpm: 178,
        hr_high_bpm: null,
        hr_source: null,
        qualifying_run_count: 0,
        sample_runs: [],
      }),
    ],
    missing: [],
    ...overrides,
  };
}

const EMPTY = { data: undefined, isLoading: false, isError: false };

describe("PaceHrZonesTable", () => {
  it("shows a loading spinner while fetching", () => {
    mockUsePaceHrZones.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<PaceHrZonesTable />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders all five zones with their labels", () => {
    mockUsePaceHrZones.mockReturnValue({ ...EMPTY, data: zones() });
    render(<PaceHrZonesTable />);
    for (const label of [
      "Recovery",
      "Basic Endurance",
      "Aerobic Threshold",
      "Lactate Threshold",
      "VO2 Max",
    ]) {
      expect(screen.getAllByText(new RegExp(label)).length).toBeGreaterThan(0);
    }
  });

  it("shows an open-ended pace range at the two open zone edges", () => {
    mockUsePaceHrZones.mockReturnValue({ ...EMPTY, data: zones() });
    render(<PaceHrZonesTable />);
    expect(screen.getByText(/Slower than 7:00 \/km/)).toBeInTheDocument();
    expect(screen.getByText(/Faster than 4:40 \/km/)).toBeInTheDocument();
  });

  it("shows a bounded pace and HR range for a fully-bounded zone", () => {
    mockUsePaceHrZones.mockReturnValue({ ...EMPTY, data: zones() });
    render(<PaceHrZonesTable />);
    // Zone 3: 320-360 s/km -> 5:20-6:00 /km.
    expect(screen.getByText("5:20–6:00 /km")).toBeInTheDocument();
    expect(screen.getByText("155–163 bpm")).toBeInTheDocument();
  });

  it("explains an empirical HR range by the number of qualifying runs", () => {
    mockUsePaceHrZones.mockReturnValue({ ...EMPTY, data: zones() });
    render(<PaceHrZonesTable />);
    expect(screen.getAllByText(/middle half .* of 5 of your own runs/).length).toBeGreaterThan(0);
  });

  it("explains a formula-fallback HR range by the shortfall in qualifying runs", () => {
    mockUsePaceHrZones.mockReturnValue({ ...EMPTY, data: zones() });
    render(<PaceHrZonesTable />);
    expect(
      screen.getByText(/Only 1 of your own runs.*estimated from your max heart rate/),
    ).toBeInTheDocument();
  });

  it("lists a zone's own sample runs, linking to the activity", () => {
    mockUsePaceHrZones.mockReturnValue({ ...EMPTY, data: zones() });
    render(<PaceHrZonesTable />);
    const links = screen.getAllByText("Tempo run");
    expect(links.length).toBeGreaterThan(0);
    const link = links[0]?.closest("a");
    expect(link).toHaveAttribute("href", "/activities/act1");
  });

  it("shows the profile VDOT and its driving activity's date", () => {
    mockUsePaceHrZones.mockReturnValue({ ...EMPTY, data: zones() });
    render(<PaceHrZonesTable />);
    expect(screen.getByText(/50\.0/)).toBeInTheDocument();
    expect(screen.getByText("2025-06-01")).toBeInTheDocument();
  });

  it("renders missing/gap messages", () => {
    mockUsePaceHrZones.mockReturnValue({
      ...EMPTY,
      data: zones({ missing: ["No heart rate data recorded yet."] }),
    });
    render(<PaceHrZonesTable />);
    expect(screen.getByText("No heart rate data recorded yet.")).toBeInTheDocument();
  });
});
