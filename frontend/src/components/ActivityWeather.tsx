// A small icon + temperature/humidity range badge for the activity detail header -- the
// "weather at the time and location of the activity" ask. Renders nothing whenever there's
// nothing real to show (no GPS start point, or the Open-Meteo fetch/parse came back empty) --
// never a fabricated range, matching CLAUDE.md's raw-first rule.
import type { ActivityWeatherOut } from "../api/types";
import { compassDirection, weatherCodeInfo } from "../weatherCode";
import { Icon } from "./Icon";

export function ActivityWeather({ weather }: { weather: ActivityWeatherOut }) {
  const {
    available,
    temperature_min_c: tMin,
    temperature_max_c: tMax,
    humidity_min_pct: hMin,
    humidity_max_pct: hMax,
    weather_code: code,
    feels_like_c: feelsLike,
    wind_speed_mps: windSpeed,
    wind_direction_deg: windDirection,
  } = weather;

  if (!available || tMin == null || tMax == null || hMin == null || hMax == null || code == null) {
    return null;
  }

  const info = weatherCodeInfo(code);

  return (
    <div className="activity-detail__weather">
      <h3 className="activity-detail__weather-heading">Weather</h3>
      <p className="activity-detail__weather-row">
        <span className="icon-chip icon-chip--xl tone-load" title={info.label}>
          <Icon name={info.icon} />
        </span>
        <span className="activity-detail__weather-readout">
          <span>{info.label}</span>
          <span>
            {Math.round(tMin) === Math.round(tMax)
              ? `${Math.round(tMin)}°C`
              : `${Math.round(tMin)}–${Math.round(tMax)}°C`}
          </span>
          {feelsLike != null && <span>Feels like {Math.round(feelsLike)}°C</span>}
          <span>
            {Math.round(hMin) === Math.round(hMax)
              ? `${Math.round(hMin)}% RH`
              : `${Math.round(hMin)}–${Math.round(hMax)}% RH`}
          </span>
          {windSpeed != null && (
            <span>
              Wind {Math.round(windSpeed)}m/s
              {windDirection != null ? ` from ${compassDirection(windDirection)}` : ""}
            </span>
          )}
        </span>
      </p>
    </div>
  );
}
