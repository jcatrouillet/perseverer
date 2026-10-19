// The cycling categories a bike can be the default for. "cycling" covers every ride; the others
// are more specific and win over it for the rides they describe (bikes.py::CATEGORIES). The
// commute category follows the athlete's own commute tag on a ride.
import type { BikeCategory } from "./api/types";

export const BIKE_CATEGORIES: { key: BikeCategory; label: string }[] = [
  { key: "cycling", label: "All cycling" },
  { key: "road", label: "Road cycling" },
  { key: "mountain", label: "Mountain biking" },
  { key: "gravel", label: "Gravel bike" },
  { key: "ebike", label: "E-bike" },
  { key: "emtb", label: "eMTB" },
  { key: "commute", label: "Bike commute" },
];

export function bikeCategoryLabel(key: BikeCategory): string {
  return BIKE_CATEGORIES.find((c) => c.key === key)?.label ?? key;
}

/** The distance a tire typically lasts, offered as the starting limit when adding one. */
export const DEFAULT_TIRE_LIMIT_KM = "5000";
