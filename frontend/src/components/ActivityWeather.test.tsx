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
    feels_like_c: null,
    wind_speed_mps: null,
    wind_direction_deg: null,
    dew_point_min_c: null,
    dew_point_max_c: null,
    solar_radiation_max_wm2: null,
    solar_radiation_mean_wm2: null,
    cloud_cover_min_pct: null,
    cloud_cover_max_pct: null,
    apparent_temperature_min_c: null,
    apparent_temperature_max_c: null,
    precipitation_mm: null,
    sunrise_utc: null,
    sunset_utc: null,
    sunset_during_run: null,
    hourly: [],
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

  it("renders a visible 'Weather' heading and an extra-large icon chip", () => {
    const { container } = render(<ActivityWeather weather={weather()} />);
    expect(screen.getByRole("heading", { name: "Weather" })).toBeInTheDocument();
    // --xl, not --lg -- the readout can run up to five lines (condition, temp, feels like,
    // humidity, wind), so the icon needs to be bigger than the standard large chip to still
    // look proportionate next to it.
    expect(container.querySelector(".icon-chip--xl")).toBeInTheDocument();
  });

  it("shows a single value, not a repeated range, when min and max are the same", () => {
    render(
      <ActivityWeather
        weather={weather({
          temperature_min_c: 20.4,
          temperature_max_c: 20.4,
          humidity_min_pct: 50.0,
          humidity_max_pct: 50.0,
        })}
      />,
    );
    expect(screen.getByText("20°C")).toBeInTheDocument();
    expect(screen.getByText("50% RH")).toBeInTheDocument();
    expect(screen.queryByText(/–/)).not.toBeInTheDocument();
  });

  it("renders feels-like and wind lines when present", () => {
    render(
      <ActivityWeather
        weather={weather({
          feels_like_c: 27.6,
          wind_speed_mps: 5.4,
          wind_direction_deg: 270,
        })}
      />,
    );
    expect(screen.getByText("Feels like 28°C")).toBeInTheDocument();
    expect(screen.getByText("Wind 5m/s from W")).toBeInTheDocument();
  });

  it("omits feels-like and wind lines when they are null", () => {
    render(<ActivityWeather weather={weather()} />);
    expect(screen.queryByText(/Feels like/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Wind/)).not.toBeInTheDocument();
  });

  it("shows wind speed without a direction when direction is missing", () => {
    render(
      <ActivityWeather weather={weather({ wind_speed_mps: 3.0, wind_direction_deg: null })} />,
    );
    expect(screen.getByText("Wind 3m/s")).toBeInTheDocument();
  });
});
