// Insights' "Threshold Analysis" tab -- independently-computed anaerobic AND aerobic threshold
// pace/HR, plus max heart rate. See performance_rollup.py's own docstring for the model (a
// rolling-VDOT-derived pair of threshold paces, each with an empirical-from-real-runs HR and a
// labeled fallback, and a 365-day rolling max HR) and vdot.py's THRESHOLD_VO2MAX_FRACTION/
// AEROBIC_THRESHOLD_VO2MAX_FRACTION for the literature behind each threshold's own fraction of
// VO2max -- deliberately never Garmin's own daily_lactate_threshold fields. Same MetricExplorer +
// TrendControls + trendWindow.ts wiring as FitnessPage.tsx. Renamed from ThresholdMaxHrChart.tsx
// once the aerobic threshold and the factor-analysis panel (ThresholdFactorAnalysis.tsx, rendered
// alongside this on the same tab) expanded this well past "threshold pace + max HR."
import { useMemo, useState } from "react";

import { usePerformance } from "../api/queries";
import type { PerformanceDailyRollupOut } from "../api/types";
import { EARLIEST_PLAUSIBLE_DATE, isoDate } from "../dateUtils";
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
import { ChartFullscreen } from "./ChartFullscreen";
import { LoadingSpinner } from "./LoadingSpinner";
import { MetricExplorer, type ExplorerMetric } from "./MetricExplorer";
import { TrendChart, type TrendSeries } from "./TrendChart";
import { TrendControls } from "./TrendControls";

function hasAnyRawValue(points: DailyPoint[], keys: string[]): boolean {
  return points.some((p) => keys.some((k) => typeof p[k] === "number"));
}

const TODAY = isoDate(new Date());

function performanceToDailyPoints(rows: PerformanceDailyRollupOut[]): DailyPoint[] {
  return rows.map((r) => ({
    local_date: r.local_date,
    threshold_pace_s_per_km: r.threshold_pace_s_per_km,
    threshold_hr_bpm: r.threshold_hr_bpm,
    aerobic_threshold_pace_s_per_km: r.aerobic_threshold_pace_s_per_km,
    aerobic_threshold_hr_bpm: r.aerobic_threshold_hr_bpm,
    max_hr_bpm: r.max_hr_bpm,
  }));
}

function formatPaceSPerKm(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")} /km`;
}

const formatBpm = (v: number) => `${v.toFixed(0)} bpm`;

const ANAEROBIC_PACE_SERIES: TrendSeries[] = [
  {
    key: "threshold_pace_s_per_km",
    label: "Anaerobic threshold pace",
    color: toneColor("pace"),
    formatValue: formatPaceSPerKm,
  },
];
const AEROBIC_PACE_SERIES: TrendSeries[] = [
  {
    key: "aerobic_threshold_pace_s_per_km",
    label: "Aerobic threshold pace",
    color: toneColor("elevation"),
    formatValue: formatPaceSPerKm,
  },
];
// Threshold HR and max HR share one chart (and one axis -- both are bpm) rather than threshold HR
// pairing with threshold pace: they're the same unit and directly comparable (threshold HR is
// necessarily below max HR), whereas pace+HR together only works via two separate axes. Distinct
// tones needed since both would otherwise render in the same "hr" hue -- same precedent
// HealthPage.tsx's own Max+Resting heart rate chart already established (its Resting line takes
// toneColor("pace") for the same reason).
const ANAEROBIC_HR_SERIES: TrendSeries[] = [
  { key: "max_hr_bpm", label: "Max heart rate", color: toneColor("hr"), formatValue: formatBpm },
  {
    key: "threshold_hr_bpm",
    label: "Anaerobic threshold HR",
    color: toneColor("pace"),
    formatValue: formatBpm,
  },
];
const AEROBIC_HR_SERIES: TrendSeries[] = [
  { key: "max_hr_bpm", label: "Max heart rate", color: toneColor("hr"), formatValue: formatBpm },
  {
    key: "aerobic_threshold_hr_bpm",
    label: "Aerobic threshold HR",
    color: toneColor("elevation"),
    formatValue: formatBpm,
  },
];

interface MetricDef {
  key: string;
  title: string;
  keys: string[];
  series: TrendSeries[];
}

const METRIC_DEFS: MetricDef[] = [
  {
    key: "anaerobic-threshold-pace",
    title: "Anaerobic threshold pace",
    keys: ["threshold_pace_s_per_km"],
    series: ANAEROBIC_PACE_SERIES,
  },
  {
    key: "aerobic-threshold-pace",
    title: "Aerobic threshold pace",
    keys: ["aerobic_threshold_pace_s_per_km"],
    series: AEROBIC_PACE_SERIES,
  },
  {
    key: "anaerobic-hr",
    title: "Anaerobic threshold & max HR",
    keys: ["threshold_hr_bpm", "max_hr_bpm"],
    series: ANAEROBIC_HR_SERIES,
  },
  {
    key: "aerobic-hr",
    title: "Aerobic threshold & max HR",
    keys: ["aerobic_threshold_hr_bpm", "max_hr_bpm"],
    series: AEROBIC_HR_SERIES,
  },
];

export function ThresholdAnalysisChart() {
  const [resolution, setResolution] = useState<Resolution>("week");
  const [anchor, setAnchor] = useState(TODAY);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const [customRange, setCustomRange] = useState<CustomRange | null>(null);

  const performance = usePerformance(EARLIEST_PLAUSIBLE_DATE, TODAY);

  const points = useMemo(
    () => performanceToDailyPoints(performance.data ?? []),
    [performance.data],
  );

  const availableMetrics = useMemo(
    () => METRIC_DEFS.filter((def) => hasAnyRawValue(points, def.keys)),
    [points],
  );
  const activeMetric =
    availableMetrics.find((d) => d.key === selectedMetric) ?? availableMetrics[0] ?? null;

  const dataStart = useMemo(
    () => (activeMetric ? earliestDateForKeys(points, activeMetric.keys) : TODAY),
    [activeMetric, points],
  );

  const effectiveCustomRange = customRange ?? defaultCustomRange(dataStart, TODAY);

  const window = useMemo(
    () => computeWindow(resolution, anchor, dataStart, TODAY, effectiveCustomRange),
    [resolution, anchor, dataStart, effectiveCustomRange],
  );

  function changeResolution(next: Resolution) {
    setResolution(next);
    if (next === "custom" && customRange === null) {
      setCustomRange(defaultCustomRange(dataStart, TODAY));
    }
  }

  const metrics: ExplorerMetric[] = useMemo(
    () =>
      availableMetrics.map((def) => {
        const bucketed = bucketSeriesToWindow(points, def.keys, window);
        return {
          key: def.key,
          title: def.title,
          content: (
            <ChartFullscreen title={def.title}>
              <TrendChart points={bucketed} series={def.series} />
            </ChartFullscreen>
          ),
        };
      }),
    [availableMetrics, points, window],
  );

  return (
    <>
      {performance.isLoading && <LoadingSpinner />}
      {performance.isError && <p role="alert">Could not load threshold analysis data.</p>}

      {!performance.isLoading && !performance.isError && metrics.length > 0 && (
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
    </>
  );
}
