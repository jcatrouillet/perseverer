// Distance/pace display, unit-aware (km or miles per the athlete's own Personalize setting).
// Before this file, every screen did its own `distance_m / 1000` + `.toFixed()` + a literal
// "km" suffix -- confirmed by a full-codebase search, no shared formatter existed. Pace still
// composes with the *existing* `formatMinPerKm` (runningStats.ts) for its "M:SS" part, so that
// function and its own ~15 callers need no changes -- this just converts to the display unit's
// own per-unit seconds first.
import { usePersonalize } from "./PersonalizeContext";
import type { UnitPreference } from "./api/types";
import { formatMinPerKm } from "./runningStats";

const METERS_PER_MILE = 1609.344;
const KM_PER_MILE = METERS_PER_MILE / 1000;

export function metersToDisplayDistance(meters: number, unit: UnitPreference): number {
  return unit === "imperial" ? meters / METERS_PER_MILE : meters / 1000;
}

/** Inverse of `metersToDisplayDistance` -- for an entry form taking a value already in the
 * display unit (km or miles) and needing to store it as meters (this app's own SI-storage
 * convention, CLAUDE.md principle 6). */
export function displayDistanceToMeters(value: number, unit: UnitPreference): number {
  return unit === "imperial" ? value * METERS_PER_MILE : value * 1000;
}

export function distanceUnitLabel(unit: UnitPreference): "km" | "mi" {
  return unit === "imperial" ? "mi" : "km";
}

export function formatDistanceValue(meters: number, unit: UnitPreference, decimals = 1): string {
  return `${metersToDisplayDistance(meters, unit).toFixed(decimals)} ${distanceUnitLabel(unit)}`;
}

/** `secPerKm` is this app's own internal pace-storage unit everywhere else (unchanged) --
 * converted to per-mile seconds first when the display unit is imperial. Bare minutes, for a
 * caller that wants to pass the "M:SS" part and a separate "/km"|"/mi" unit to something like
 * StatTile's own value/unit split, rather than one combined string. */
export function paceMinPerDisplayUnit(secPerKm: number, unit: UnitPreference): number {
  return (unit === "imperial" ? secPerKm * KM_PER_MILE : secPerKm) / 60;
}

export function formatPaceValue(secPerKm: number, unit: UnitPreference): string {
  return `${formatMinPerKm(paceMinPerDisplayUnit(secPerKm, unit))} /${distanceUnitLabel(unit)}`;
}

/** km/h (this app's own internal speed-storage unit for wheeled sports, unchanged) -> mph for
 * imperial. */
export function kmhToDisplaySpeed(kmh: number, unit: UnitPreference): number {
  return unit === "imperial" ? kmh / KM_PER_MILE : kmh;
}

export function speedUnitLabel(unit: UnitPreference): "km/h" | "mph" {
  return unit === "imperial" ? "mph" : "km/h";
}

/** Convenience hook: the same functions, pre-bound to the athlete's own current unit
 * preference, so a call site just does `formatDistance(activity.distance_m)`. */
export function useDistanceFormat() {
  const { unit_preference: unit } = usePersonalize();
  return {
    unit,
    unitLabel: distanceUnitLabel(unit),
    speedUnitLabel: speedUnitLabel(unit),
    formatDistance: (meters: number, decimals = 1) => formatDistanceValue(meters, unit, decimals),
    metersToDisplay: (meters: number) => metersToDisplayDistance(meters, unit),
    displayToMeters: (value: number) => displayDistanceToMeters(value, unit),
    formatPace: (secPerKm: number) => formatPaceValue(secPerKm, unit),
    paceMinPerDisplayUnit: (secPerKm: number) => paceMinPerDisplayUnit(secPerKm, unit),
    kmhToDisplay: (kmh: number) => kmhToDisplaySpeed(kmh, unit),
  };
}
