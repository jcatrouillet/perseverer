import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActivityWeatherOut } from "../api/types";
import { ActivityWeather } from "./ActivityWeather";

function weather(overrides: Partial<ActivityWeatherOut> = {}): ActivityWeatherOut {
  return {
    available: true,
    temperature_min_c: 17.3,
    temperature_max_c: 25.1,
    humidity_min_pct: 45.0,
    humidity_max_pct: 71.0,
    weather_code: 0,
    ...overrides,
  };
}

describe("ActivityWeather", () => {
  it("renders the rounded temperature and humidity ranges", () => {
    const { container } = render(<ActivityWeather weather={weather()} />);
    expect(screen.getByText("17–25°C")).toBeInTheDocument();
    expect(screen.getByText("45–71% RH")).toBeInTheDocument();
    expect(container.querySelector(".icon-chip")).toBeInTheDocument();
  });

  it("renders nothing when unavailable", () => {
    const { container } = render(<ActivityWeather weather={weather({ available: false })} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when a required field is missing despite available=true", () => {
    const { container } = render(
      <ActivityWeather weather={weather({ temperature_min_c: null })} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("uses the clear-sky sun icon for weather_code 0", () => {
    render(<ActivityWeather weather={weather({ weather_code: 0 })} />);
    expect(screen.getByTitle("Clear sky")).toBeInTheDocument();
  });
});
