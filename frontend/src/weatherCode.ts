// WMO weather code -> icon/label, per Open-Meteo's own documented code table
// (https://open-meteo.com/en/docs -- the same taxonomy the archive API returns, confirmed
// against a real response before wiring this up). Backend stores the raw code as-is (weather.py's
// own docstring); this mapping is purely a frontend presentation concern.
import type { IconName } from "./components/Icon";

interface WeatherCodeInfo {
  icon: IconName;
  label: string;
}

const CODE_TABLE: Record<number, WeatherCodeInfo> = {
  0: { icon: "sun", label: "Clear sky" },
  1: { icon: "sun", label: "Mainly clear" },
  2: { icon: "cloud", label: "Partly cloudy" },
  3: { icon: "cloud", label: "Overcast" },
  45: { icon: "fog", label: "Fog" },
  48: { icon: "fog", label: "Depositing rime fog" },
  51: { icon: "rain", label: "Light drizzle" },
  53: { icon: "rain", label: "Moderate drizzle" },
  55: { icon: "rain", label: "Dense drizzle" },
  56: { icon: "rain", label: "Light freezing drizzle" },
  57: { icon: "rain", label: "Dense freezing drizzle" },
  61: { icon: "rain", label: "Slight rain" },
  63: { icon: "rain", label: "Moderate rain" },
  65: { icon: "rain", label: "Heavy rain" },
  66: { icon: "rain", label: "Light freezing rain" },
  67: { icon: "rain", label: "Heavy freezing rain" },
  71: { icon: "snow", label: "Slight snow fall" },
  73: { icon: "snow", label: "Moderate snow fall" },
  75: { icon: "snow", label: "Heavy snow fall" },
  77: { icon: "snow", label: "Snow grains" },
  80: { icon: "rain", label: "Slight rain showers" },
  81: { icon: "rain", label: "Moderate rain showers" },
  82: { icon: "rain", label: "Violent rain showers" },
  85: { icon: "snow", label: "Slight snow showers" },
  86: { icon: "snow", label: "Heavy snow showers" },
  95: { icon: "storm", label: "Thunderstorm" },
  96: { icon: "storm", label: "Thunderstorm with slight hail" },
  99: { icon: "storm", label: "Thunderstorm with heavy hail" },
};

const FALLBACK: WeatherCodeInfo = { icon: "cloud", label: "Unknown conditions" };

export function weatherCodeInfo(code: number): WeatherCodeInfo {
  return CODE_TABLE[code] ?? FALLBACK;
}

const COMPASS_POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] as const;

// Degrees (meteorological convention: the direction the wind is *coming from*) -> 8-point compass.
export function compassDirection(degrees: number): string {
  const normalized = ((degrees % 360) + 360) % 360;
  const index = Math.round(normalized / 45) % 8;
  return COMPASS_POINTS[index];
}
