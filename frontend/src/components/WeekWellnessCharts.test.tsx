import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HealthDashboardMetricOut, SleepSessionOut } from "../api/types";
import { WeekWellnessCharts } from "./WeekWellnessCharts";

function metric(
  logical_metric: string,
  days: { local_date: string; value_avg: number }[],
): HealthDashboardMetricOut {
  return {
    logical_metric,
    last_observed: days.at(-1)?.local_date ?? null,
    daily: days.map((d) => ({
      local_date: d.local_date,
      value_sum: d.value_avg,
      value_avg: d.value_avg,
      value_min: d.value_avg,
      value_max: d.value_avg,
      value_last: d.value_avg,
      n_observations: 1,
      source_metric_key: "test",
    })),
  };
}

function sleepSession(local_date: string, total_sleep_s: number): SleepSessionOut {
  return {
    local_date,
    start_time_utc: `${local_date}T22:00:00Z`,
    end_time_utc: `${local_date}T06:00:00Z`,
    total_sleep_s,
    sleep_score: null,
    source: "test",
    stages: [],
  };
}

describe("WeekWellnessCharts", () => {
  it("renders nothing when none of resting HR, stress, sleep, or respiration have data", () => {
    const { container } = render(
      <WeekWellnessCharts
        metrics={[]}
        sleepSessions={[]}
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows only the sections that actually have data for the week", () => {
    render(
      <WeekWellnessCharts
        metrics={[metric("resting_heart_rate", [{ local_date: "2025-06-03", value_avg: 48 }])]}
        sleepSessions={[]}
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
      />,
    );
    expect(screen.getByText("Resting heart rate")).toBeInTheDocument();
    expect(screen.queryByText("Stress")).not.toBeInTheDocument();
    expect(screen.queryByText("Sleep duration")).not.toBeInTheDocument();
    expect(screen.queryByText("Respiration (sleep vs waking)")).not.toBeInTheDocument();
  });

  it("shows sleep duration built from GET /sleep sessions, not the health-dashboard metrics", () => {
    render(
      <WeekWellnessCharts
        metrics={[]}
        sleepSessions={[sleepSession("2025-06-03", 27000)]} // 7.5h
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
      />,
    );
    expect(screen.getByText("Sleep duration")).toBeInTheDocument();
  });

  it("shows the respiration section when either sleep or waking respiration has data", () => {
    render(
      <WeekWellnessCharts
        metrics={[
          metric("sleep_respiration_rate", [{ local_date: "2025-06-03", value_avg: 13.4 }]),
        ]}
        sleepSessions={[]}
        weekStart="2025-06-02"
        weekEnd="2025-06-08"
      />,
    );
    expect(screen.getByText("Respiration (sleep vs waking)")).toBeInTheDocument();
  });
});
