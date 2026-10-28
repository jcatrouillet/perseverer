import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PersonalizeSettingsOut } from "../api/types";
import { PersonalizeContext } from "../PersonalizeContext";
import { InsightsPage } from "./InsightsPage";

const mockUseAllActivities = vi.fn((_filters: { sport?: string }) => ({
  data: [] as { id: string; is_commute?: boolean }[],
  isLoading: false,
  isError: false,
}));

vi.mock("../api/queries", () => ({
  useAllActivities: (filters: { sport?: string }) => mockUseAllActivities(filters),
}));

// Each tab's chart is tested on its own; here they only report which sport they were given.
vi.mock("../components/PaceTrendsChart", () => ({
  PaceTrendsChart: () => <div>pace trends</div>,
}));
vi.mock("../components/PowerTrendsChart", () => ({
  PowerTrendsChart: () => <div>power trends</div>,
}));
vi.mock("../components/TrainingBandsChart", () => ({
  TrainingBandsChart: ({ sport }: { sport: string }) => <div>bands for {sport}</div>,
}));
vi.mock("../components/PrProgressChart", () => ({
  PrProgressChart: ({ activities }: { activities: { id: string }[] }) => (
    <div>pr rides: {activities.map((a) => a.id).join(",")}</div>
  ),
}));
vi.mock("../components/RacePredictionsChart", () => ({ RacePredictionsChart: () => null }));
vi.mock("../components/RaceReadinessChart", () => ({ RaceReadinessChart: () => null }));
vi.mock("../components/PaceHrZonesTable", () => ({ PaceHrZonesTable: () => null }));
vi.mock("../components/Vo2maxChart", () => ({ Vo2maxChart: () => <div>vo2max chart</div> }));
vi.mock("../components/Vo2maxFactorAnalysis", () => ({ Vo2maxFactorAnalysis: () => null }));
vi.mock("../components/PerformanceCurveChart", () => ({ PerformanceCurveChart: () => null }));
vi.mock("../components/EddingtonChart", () => ({ EddingtonChart: () => null }));
vi.mock("../components/CyclingPowerPanel", () => ({
  CyclingPowerPanel: () => <div>ftp panel</div>,
}));

function renderWith(insights_sport: "running" | "cycling", commute_filter = false) {
  const settings: PersonalizeSettingsOut = {
    week_start_day: "monday",
    time_format: "24h",
    default_view: "week",
    unit_preference: "metric",
    insights_sport,
    commute_filter,
    map_style: "light",
  };
  return render(
    <PersonalizeContext.Provider value={settings}>
      <InsightsPage />
    </PersonalizeContext.Provider>,
  );
}

describe("InsightsPage", () => {
  it("opens on the sport chosen in Settings", () => {
    renderWith("cycling");
    expect(screen.getByRole("button", { name: /Cycling/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("power trends")).toBeInTheDocument();
    expect(mockUseAllActivities).toHaveBeenLastCalledWith({ sport: "cycling" });
  });

  it("switching to cycling hides the running-only tabs and passes the sport on", () => {
    renderWith("running");
    expect(screen.getByRole("tab", { name: "VO2max" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Training bands" }));
    expect(screen.getByText("bands for running")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Cycling/ }));
    expect(screen.queryByRole("tab", { name: "VO2max" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Race predictions" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "FTP & VO2max" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Power trends" })).toBeInTheDocument();
    expect(screen.getByText("bands for cycling")).toBeInTheDocument();
  });

  it("the VO2max tab becomes FTP & VO2max for cycling", () => {
    renderWith("running");
    fireEvent.click(screen.getByRole("tab", { name: "VO2max" }));
    expect(screen.getByText("vo2max chart")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Cycling/ }));
    expect(screen.getByText("ftp panel")).toBeInTheDocument();
  });

  it("falls back to the first tab when a running-only tab is open and the switch moves", () => {
    renderWith("running");
    fireEvent.click(screen.getByRole("tab", { name: "Race predictions" }));
    fireEvent.click(screen.getByRole("button", { name: /Cycling/ }));
    expect(screen.getByText("power trends")).toBeInTheDocument();
  });

  it("offers no commute toggle unless Settings turned the filter on", () => {
    renderWith("running", false);
    expect(screen.queryByLabelText("Exclude commutes")).not.toBeInTheDocument();
  });

  it("leaves tagged commutes out of the activity-based tabs while the toggle is checked", () => {
    localStorage.clear();
    mockUseAllActivities.mockReturnValue({
      data: [
        { id: "training", is_commute: false },
        { id: "commute", is_commute: true },
      ],
      isLoading: false,
      isError: false,
    });
    renderWith("running", true);
    fireEvent.click(screen.getByRole("tab", { name: "PR progress" }));
    expect(screen.getByText("pr rides: training,commute")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Exclude commutes"));
    expect(screen.getByText("pr rides: training")).toBeInTheDocument();
    localStorage.clear();
  });
});
