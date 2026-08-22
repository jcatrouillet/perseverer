"""WMO weather code -> label/emoji. Labels are the same 27 codes + fallback as
`frontend/src/weatherCode.ts`'s own table, deliberately duplicated across languages rather than
shared code (no cross-language module boundary in this project -- same precedent as
`pace_bands.py`'s frontend `BAND_COLORS` duplicating band definitions) -- keep the two in sync by
hand if Open-Meteo's code table ever changes.

The backend only needs this for one thing: prepending a condition emoji to an activity's own
title (see `weather_titles.py`). Everywhere else in the read API the raw WMO code is passed
through as-is and left for the frontend to present, per `weather.py`'s own module docstring.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeatherCodeInfo:
    label: str
    emoji: str


_CODE_TABLE: dict[int, WeatherCodeInfo] = {
    0: WeatherCodeInfo("Clear sky", "☀️"),
    1: WeatherCodeInfo("Mainly clear", "🌤️"),
    2: WeatherCodeInfo("Partly cloudy", "⛅"),
    3: WeatherCodeInfo("Overcast", "☁️"),
    45: WeatherCodeInfo("Fog", "🌫️"),
    48: WeatherCodeInfo("Depositing rime fog", "🌫️"),
    51: WeatherCodeInfo("Light drizzle", "🌦️"),
    53: WeatherCodeInfo("Moderate drizzle", "🌦️"),
    55: WeatherCodeInfo("Dense drizzle", "🌦️"),
    56: WeatherCodeInfo("Light freezing drizzle", "🌦️"),
    57: WeatherCodeInfo("Dense freezing drizzle", "🌦️"),
    61: WeatherCodeInfo("Slight rain", "🌧️"),
    63: WeatherCodeInfo("Moderate rain", "🌧️"),
    65: WeatherCodeInfo("Heavy rain", "🌧️"),
    66: WeatherCodeInfo("Light freezing rain", "🌧️"),
    67: WeatherCodeInfo("Heavy freezing rain", "🌧️"),
    71: WeatherCodeInfo("Slight snow fall", "🌨️"),
    73: WeatherCodeInfo("Moderate snow fall", "🌨️"),
    75: WeatherCodeInfo("Heavy snow fall", "🌨️"),
    77: WeatherCodeInfo("Snow grains", "🌨️"),
    80: WeatherCodeInfo("Slight rain showers", "🌦️"),
    81: WeatherCodeInfo("Moderate rain showers", "🌦️"),
    82: WeatherCodeInfo("Violent rain showers", "⛈️"),
    85: WeatherCodeInfo("Slight snow showers", "🌨️"),
    86: WeatherCodeInfo("Heavy snow showers", "🌨️"),
    95: WeatherCodeInfo("Thunderstorm", "⛈️"),
    96: WeatherCodeInfo("Thunderstorm with slight hail", "⛈️"),
    99: WeatherCodeInfo("Thunderstorm with heavy hail", "⛈️"),
}

_FALLBACK = WeatherCodeInfo("Unknown conditions", "🌡️")


def weather_code_info(code: int) -> WeatherCodeInfo:
    return _CODE_TABLE.get(code, _FALLBACK)
