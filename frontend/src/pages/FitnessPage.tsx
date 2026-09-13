// Fitness & Form: a metric list (VO2max, HRV, Lactate threshold, and the combined Fitness/
// Fatigue/Form triad) on the left, one selected metric's chart on the right, both sharing one
// Week/Month/Year/All-time resolution + time-window navigator (TrendControls) above the chart --
// see trendWindow.ts's own docstring for why the bucketing/windowing happens client-side against
// one full-history fetch rather than a per-window request, and components/MetricExplorer.tsx for
// the list+chart split itself. Replaced an earlier version that showed every metric's chart
// stacked at once, per the user's own request to pick one from a list instead.
import { useMemo, useState } from "react";

import { useFitness, useHealthDashboard } from "../api/queries";
import type { FitnessDailyRollupOut } from "../api/types";
import { ChartFullscreen } from "../components/ChartFullscreen";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { MetricExplorer, type ExplorerMetric } from "../components/MetricExplorer";
import { TrendChart, type TrendSeries } from "../components/TrendChart";
import { TrendControls } from "../components/TrendControls";
import { EARLIEST_PLAUSIBLE_DATE, isoDate } from "../dateUtils";
import { mergeTrendSeries } from "../healthStats";
import { toneColor } from "../metricStyle";
import {
  bucketSeriesToWindow,
  computeWindow,
  defaultCustomRange,
  earliestDateForKeys,
  shiftAnchor,
  type CustomRange,
  type DailyPoint,
  type Resolution,
} from "../trendWindow";

/** Whether any of `keys` has a real value anywhere in the athlete's fetched history -- gates
 * which metrics appear in the left-hand list at all. Deliberately checked against the *raw*,
 * unwindowed daily points (not the current resolution/window's own bucketed points): a metric
 * with data somewhere just not in this particular week should still be pickable, not disappear
 * the moment you're looking at a quiet week. */
function hasAnyRawValue(points: DailyPoint[], keys: string[]): boolean {
  return points.some((p) => keys.some((k) => typeof p[k] === "number"));
}

const TODAY = isoDate(new Date());

function fitnessToDailyPoints(series: FitnessDailyRollupOut[]): DailyPoint[] {
  return series.map((s) => ({
    local_date: s.local_date,
    ctl: s.ctl,
    atl: s.atl,
    tsb: s.tsb,
  }));
}

function formatPaceFromRawSpeed(rawSpeed: number): string {
  // See health/json_parser.py::parse_daily_lactate_threshold_json's own docstring: Garmin's raw
  // value here is not plain m/s -- x10 first, confirmed against real account data.
  const mps = rawSpeed * 10;
  if (mps <= 0) return "—";
  const paceSPerKm = 1000 / mps;
  const m = Math.floor(paceSPerKm / 60);
  const s = Math.round(paceSPerKm % 60);
  return `${m}:${String(s).padStart(2, "0")} /km`;
}

const VO2MAX_SERIES: TrendSeries[] = [
  { key: "vo2max", label: "VO2max", color: toneColor("cadence") },
];
const HRV_SERIES: TrendSeries[] = [
  { key: "hrv_nightly_average", label: "HRV", color: toneColor("cadence") },
];
const LACTATE_THRESHOLD_SERIES: TrendSeries[] = [
  {
    key: "lactate_threshold_speed",
    label: "Threshold pace",
    color: toneColor("pace"),
    axis: "left",
    formatValue: formatPaceFromRawSpeed,
  },
  {
    key: "lactate_threshold_heart_rate",
    label: "Threshold HR",
    color: toneColor("hr"),
    axis: "right",
    formatValue: (v) => `${v.toFixed(0)} bpm`,
  },
];
const FITNESS_FORM_SERIES: TrendSeries[] = [
  { key: "ctl", label: "Fitness (CTL)", color: toneColor("elevation"), kind: "area", axis: "left" },
  { key: "atl", label: "Fatigue (ATL)", color: toneColor("load"), axis: "left" },
  { key: "tsb", label: "Form (TSB)", color: toneColor("pace"), axis: "right" },
];

interface MetricDef {
  key: string;
  title: string;
  source: "fitness" | "health";
  keys: string[];
  series: TrendSeries[];
  referenceZeroAxis?: "left" | "right";
}

const METRIC_DEFS: MetricDef[] = [
  {
    key: "fitness-form",
    title: "Fitness, Fatigue & Form",
    source: "fitness",
    keys: ["ctl", "atl", "tsb"],
    series: FITNESS_FORM_SERIES,
    referenceZeroAxis: "right",
  },
  { key: "vo2max", title: "VO2max", source: "health", keys: ["vo2max"], series: VO2MAX_SERIES },
  {
    key: "hrv",
    title: "HRV",
    source: "health",
    keys: ["hrv_nightly_average"],
    series: HRV_SERIES,
  },
  {
    key: "lactate-threshold",
    title: "Lactate threshold",
    source: "health",
    keys: ["lactate_threshold_speed", "lactate_threshold_heart_rate"],
    series: LACTATE_THRESHOLD_SERIES,
  },
];

export function FitnessPage() {
  const [resolution, setResolution] = useState<Resolution>("week");
  const [anchor, setAnchor] = useState(TODAY);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const [customRange, setCustomRange] = useState<CustomRange | null>(null);

  const fitness = useFitness(EARLIEST_PLAUSIBLE_DATE, TODAY);
  const dashboard = useHealthDashboard(EARLIEST_PLAUSIBLE_DATE, TODAY);

  const fitnessPoints = useMemo(
    () => fitnessToDailyPoints(fitness.data ?? []),
    [fitness.data],
  );
  const healthPoints = useMemo(
    () =>
      dashboard.data
        ? mergeTrendSeries(dashboard.data.metrics, [
            "vo2max",
            "hrv_nightly_average",
            "lactate_threshold_speed",
            "lactate_threshold_heart_rate",
          ])
        : [],
    [dashboard.data],
  );

  const sourceFor = (def: MetricDef) => (def.source === "fitness" ? fitnessPoints : healthPoints);

  // Only the metrics that actually have data anywhere -- same list MetricExplorer would resolve
  // `selected` against, computed here too so the active metric's own history bounds can be used
  // below rather than the whole page's combined earliest date.
  const availableMetrics = useMemo(
    () => METRIC_DEFS.filter((def) => hasAnyRawValue(sourceFor(def), def.keys)),
    [fitnessPoints, healthPoints],
  );
  const activeMetric =
    availableMetrics.find((d) => d.key === selectedMetric) ?? availableMetrics[0] ?? null;

  // Scoped to the *selected* metric's own keys -- not a merge across fitness + health -- so
  // "All time" for, say, Lactate threshold starts where that metric's own data starts, not
  // wherever the CTL/ATL/TSB rollup (which can run much further back) happens to begin.
  const dataStart = useMemo(
    () => (activeMetric ? earliestDateForKeys(sourceFor(activeMetric), activeMetric.keys) : TODAY),
    [activeMetric, fitnessPoints, healthPoints],
  );

  // Seeded lazily (only once the athlete actually switches to Custom) rather than on every
  // render, since it depends on `dataStart`, which itself depends on the active metric.
  const effectiveCustomRange = customRange ?? defaultCustomRange(dataStart, TODAY);

  const window = useMemo(
    () => computeWindow(resolution, anchor, dataStart, TODAY, effectiveCustomRange),
    [resolution, anchor, dataStart, effectiveCustomRange],
  );

  const isLoading = fitness.isLoading || dashboard.isLoading;
  const isError = fitness.isError || dashboard.isError;

  function changeResolution(next: Resolution) {
    // Deliberately keep `anchor` as-is: it already names a date within whatever period is
    // currently in view (TODAY on first load, or wherever prev/next navigated to since), and
    // computeWindow re-derives each resolution's own start/end from it -- so switching from a
    // week in August 2025 to Month lands on August 2025, not back to the current month.
    setResolution(next);
    if (next === "custom" && customRange === null) {
      setCustomRange(defaultCustomRange(dataStart, TODAY));
    }
  }

  const metrics: ExplorerMetric[] = useMemo(
    () =>
      availableMetrics.map((def) => {
        const points = bucketSeriesToWindow(sourceFor(def), def.keys, window);
        return {
          key: def.key,
          title: def.title,
          content: (
            <ChartFullscreen title={def.title}>
              <TrendChart
                points={points}
                series={def.series}
                referenceZeroAxis={def.referenceZeroAxis}
              />
            </ChartFullscreen>
          ),
        };
      }),
    [availableMetrics, window],
  );

  return (
    <main>
      <h1>Fitness &amp; Form</h1>

      {isLoading && <LoadingSpinner />}
      {isError && <p role="alert">Could not load Fitness &amp; Form.</p>}

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
              customRange={effectiveCustomRange}
              onCustomRangeChange={setCustomRange}
              dataStart={dataStart}
              today={TODAY}
            />
          }
        />
      )}
    </main>
  );
}
