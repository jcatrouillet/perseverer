// Health: a metric list (weight through protein ratio) on the left, one selected metric's chart
// on the right, both sharing one Week/Month/Year/All-time resolution + time-window navigator
// (TrendControls) above the chart -- the exact same pattern as FitnessPage.tsx, per the user's
// own request to pick one metric from a list rather than see every chart stacked at once. See
// components/MetricExplorer.tsx for the list+chart split itself.
//
// `CORE_METRICS`/`HRV_METRIC`/`WEIGHT_METRIC` stay exported unchanged -- MonthView/YearView/
// AllTimeView's own embedded "Health" calendar cards import these for their own (unrelated,
// untouched) tile-grid/trend-chart rendering, which this rewrite doesn't touch.
import { useMemo, useState } from "react";

import { useHealthDashboard, useSleep } from "../api/queries";
import type { SleepSessionOut } from "../api/types";
import { ChartFullscreen } from "../components/ChartFullscreen";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { MetricExplorer, type ExplorerMetric } from "../components/MetricExplorer";
import { TrendChart, type TrendSeries } from "../components/TrendChart";
import { TrendControls } from "../components/TrendControls";
import { EARLIEST_PLAUSIBLE_DATE, isoDate } from "../dateUtils";
import { mergeTrendSeries } from "../healthStats";
import { healthMetricStyle, toneColor } from "../metricStyle";
import {
  bucketSeriesToWindow,
  computeWindow,
  earliestDateForKeys,
  shiftAnchor,
  type DailyPoint,
  type Resolution,
} from "../trendWindow";

// Exported so other pages (e.g. YearView's year-in-review stats) can reuse the exact same
// categorization rather than maintaining a second, driftable copy of this list.
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

const TODAY = isoDate(new Date());

function color(logicalMetric: string): string {
  return toneColor(healthMetricStyle(logicalMetric).tone);
}

function series(
  key: string,
  label: string,
  formatValue: (v: number) => string,
  colorOverride?: string,
): TrendSeries {
  return { key, label, color: colorOverride ?? color(key), formatValue };
}

/** Whether any of `keys` has a real value anywhere in the athlete's fetched history -- gates
 * which metrics appear in the left-hand list at all, same reasoning as FitnessPage.tsx's own
 * identical helper: checked against the raw, unwindowed points, not the current window's own
 * bucketed ones, so a metric with data somewhere doesn't disappear during a quiet week. */
function hasAnyRawValue(points: DailyPoint[], keys: string[]): boolean {
  return points.some((p) => keys.some((k) => typeof p[k] === "number"));
}

function sleepToDailyPoints(sessions: SleepSessionOut[]): DailyPoint[] {
  return sessions
    .filter((s) => s.total_sleep_s != null)
    .map((s) => ({ local_date: s.local_date, sleep_hours: s.total_sleep_s! / 3600 }));
}

const oneDecimal = (unit: string) => (v: number) => `${v.toFixed(1)}${unit}`;
const wholeNumber = (unit: string) => (v: number) => `${v.toFixed(0)}${unit}`;

// One list entry per requested metric -- combined two-line entries (Respiration, Heart rate,
// Blood pressure) match the "Heart rate (max and resting)" combining the user asked for
// verbatim, applied consistently to the other two paired readings this app happens to carry.
const CHARTS: { key: string; title: string; keys: string[]; series: TrendSeries[] }[] = [
  {
    key: "weight",
    title: "Weight",
    keys: ["weight_kg"],
    series: [series("weight_kg", "Weight", oneDecimal(" kg"))],
  },
  { key: "bmi", title: "BMI", keys: ["bmi"], series: [series("bmi", "BMI", oneDecimal(""))] },
  {
    key: "sleep",
    title: "Sleep time",
    keys: ["sleep_hours"],
    series: [series("sleep_hours", "Sleep", oneDecimal("h"))],
  },
  {
    key: "spo2",
    title: "Pulse Ox",
    keys: ["spo2_average"],
    series: [series("spo2_average", "SpO2", wholeNumber("%"))],
  },
  {
    key: "respiration",
    title: "Respiration",
    keys: ["waking_respiration_rate", "sleep_respiration_rate"],
    series: [
      series("waking_respiration_rate", "Waking", wholeNumber(" brpm")),
      series("sleep_respiration_rate", "Sleep", wholeNumber(" brpm")),
    ],
  },
  {
    key: "heart-rate",
    title: "Heart rate",
    keys: ["max_heart_rate", "resting_heart_rate"],
    // max_heart_rate/resting_heart_rate share one "hr" tone in metricStyle.ts (both are the
    // same metric family everywhere else in the app), which would make this one combined chart's
    // two lines indistinguishable -- Resting gets an explicit second colour just for this pairing
    // rather than the shared single-metric tone.
    series: [
      series("max_heart_rate", "Max", wholeNumber(" bpm")),
      series("resting_heart_rate", "Resting", wholeNumber(" bpm"), toneColor("pace")),
    ],
  },
  {
    key: "blood-pressure",
    title: "Blood pressure",
    keys: ["blood_pressure_systolic", "blood_pressure_diastolic"],
    series: [
      series("blood_pressure_systolic", "Systolic", wholeNumber(" mmHg")),
      series("blood_pressure_diastolic", "Diastolic", wholeNumber(" mmHg")),
    ],
  },
  {
    key: "steps",
    title: "Steps",
    keys: ["steps"],
    series: [series("steps", "Steps", wholeNumber(""))],
  },
  {
    key: "body-fat",
    title: "Body fat %",
    keys: ["body_fat_pct"],
    series: [series("body_fat_pct", "Body fat", oneDecimal("%"))],
  },
  {
    key: "muscle-mass",
    title: "Muscle mass",
    keys: ["muscle_mass_kg"],
    series: [series("muscle_mass_kg", "Muscle mass", oneDecimal(" kg"))],
  },
  {
    key: "bone-mass",
    title: "Bone mass",
    keys: ["bone_mass_kg"],
    series: [series("bone_mass_kg", "Bone mass", oneDecimal(" kg"))],
  },
  {
    key: "water",
    title: "Water %",
    keys: ["water_pct"],
    series: [series("water_pct", "Water", oneDecimal("%"))],
  },
  {
    key: "bmr",
    title: "BMR",
    keys: ["bmr_kcal"],
    series: [series("bmr_kcal", "BMR", wholeNumber(" kcal"))],
  },
  {
    key: "visceral-fat",
    title: "Visceral fat",
    keys: ["visceral_fat"],
    series: [series("visceral_fat", "Visceral fat", oneDecimal(""))],
  },
  {
    key: "metabolic-age",
    title: "Metabolic age",
    keys: ["metabolic_age"],
    series: [series("metabolic_age", "Metabolic age", wholeNumber(""))],
  },
  {
    key: "protein-ratio",
    title: "Protein ratio %",
    keys: ["protein_ratio_pct"],
    series: [series("protein_ratio_pct", "Protein ratio", oneDecimal("%"))],
  },
];

const ALL_DASHBOARD_KEYS = CHARTS.flatMap((c) => c.keys).filter((k) => k !== "sleep_hours");

export function HealthPage() {
  const [resolution, setResolution] = useState<Resolution>("week");
  const [anchor, setAnchor] = useState(TODAY);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);

  const dashboard = useHealthDashboard(EARLIEST_PLAUSIBLE_DATE, TODAY);
  const sleep = useSleep(EARLIEST_PLAUSIBLE_DATE, TODAY);

  const dashboardPoints = useMemo(
    () => (dashboard.data ? mergeTrendSeries(dashboard.data.metrics, ALL_DASHBOARD_KEYS) : []),
    [dashboard.data],
  );
  const sleepPoints = useMemo(() => sleepToDailyPoints(sleep.data ?? []), [sleep.data]);

  const sourceForChart = (chart: (typeof CHARTS)[number]) =>
    chart.keys.includes("sleep_hours") ? sleepPoints : dashboardPoints;

  // Only the charts that actually have data anywhere -- same list MetricExplorer would resolve
  // `selected` against, computed here too so the active chart's own history bounds can be used
  // below rather than the whole page's combined earliest date.
  const availableCharts = useMemo(
    () => CHARTS.filter((chart) => hasAnyRawValue(sourceForChart(chart), chart.keys)),
    [dashboardPoints, sleepPoints],
  );
  const activeChart =
    availableCharts.find((c) => c.key === selectedMetric) ?? availableCharts[0] ?? null;

  // Scoped to the *selected* metric's own keys -- not a merge across every metric on the page --
  // so "All time" for Sleep starts where sleep data actually starts (e.g. 2022), not wherever
  // some unrelated metric (e.g. a 2016 weight reading) happens to begin.
  const dataStart = useMemo(
    () => (activeChart ? earliestDateForKeys(sourceForChart(activeChart), activeChart.keys) : TODAY),
    [activeChart, dashboardPoints, sleepPoints],
  );
  const window = useMemo(
    () => computeWindow(resolution, anchor, dataStart, TODAY),
    [resolution, anchor, dataStart],
  );

  const isLoading = dashboard.isLoading || sleep.isLoading;
  const isError = dashboard.isError || sleep.isError;

  function changeResolution(next: Resolution) {
    // Deliberately keep `anchor` as-is: it already names a date within whatever period is
    // currently in view (TODAY on first load, or wherever prev/next navigated to since), and
    // computeWindow re-derives each resolution's own start/end from it -- so switching from a
    // week in August 2025 to Month lands on August 2025, not back to the current month.
    setResolution(next);
  }

  const metrics: ExplorerMetric[] = useMemo(
    () =>
      availableCharts.map((chart) => {
        const points = bucketSeriesToWindow(sourceForChart(chart), chart.keys, window);
        return {
          key: chart.key,
          title: chart.title,
          content: (
            <ChartFullscreen title={chart.title}>
              <TrendChart points={points} series={chart.series} />
            </ChartFullscreen>
          ),
        };
      }),
    [availableCharts, window],
  );

  return (
    <main>
      <h1>Health</h1>

      {isLoading && <LoadingSpinner />}
      {isError && <p role="alert">Could not load the health dashboard.</p>}

      {!isLoading && !isError && metrics.length > 0 && (
        <MetricExplorer
          metrics={metrics}
          selected={selectedMetric}
          onSelect={setSelectedMetric}
          detailHeader={
            <TrendControls
              window={window}
              onResolutionChange={changeResolution}
              onPrevious={() => setAnchor(shiftAnchor(window, -1))}
              onNext={() => setAnchor(shiftAnchor(window, 1))}
            />
          }
        />
      )}
    </main>
  );
}
