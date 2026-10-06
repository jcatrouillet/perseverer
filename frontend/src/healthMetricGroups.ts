// The logical health metrics the calendar's Month/Year/All-time "Health" cards show. Kept in their
// own tiny module (not HealthPage.tsx, where they used to live) so those calendar views don't pull
// the whole Health page -- blood-test explorer and all -- into the main bundle.
export const CORE_METRICS = [
  "steps",
  "calories",
  "resting_heart_rate",
  "max_heart_rate",
  "floors_ascended",
  "vo2max",
];
export const HRV_METRIC = ["hrv_nightly_average"];
export const WEIGHT_METRIC = ["weight_kg"];
