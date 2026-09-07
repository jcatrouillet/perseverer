// Independently-computed threshold pace/HR and max heart rate -- see performance_rollup.py's own
// docstring for the model (a rolling-VDOT-derived threshold pace, an empirical-from-real-runs
// threshold HR with a labeled fallback, and a 365-day rolling max HR) and why it deliberately
// never uses Garmin's own daily_lactate_threshold fields. Same MetricExplorer + TrendControls +
// trendWindow.ts wiring as FitnessPage.tsx.
import { useMemo, useState } from "react";

import { usePerformance } from "../api/queries";
import type { PerformanceDailyRollupOut } from "../api/types";
import { EARLIEST_PLAUSIBLE_DATE, isoDate } from "../dateUtils";
import { toneColor } from "../metricStyle";
import {
  bucketSeriesToWindow,
  computeWindow,
  earliestDateForKeys,
  shiftAnchor,
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
    max_hr_bpm: r.max_hr_bpm,
  }));
}

function formatPaceSPerKm(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")} /km`;
}

const formatBpm = (v: number) => `${v.toFixed(0)} bpm`;

const THRESHOLD_PACE_SERIES: TrendSeries[] = [
  {
    key: "threshold_pace_s_per_km",
    label: "Threshold pace",
    color: toneColor("pace"),
    formatValue: formatPaceSPerKm,
  },
];
// Threshold HR and max HR share one chart (and one axis -- both are bpm) rather than threshold HR
// pairing with threshold pace: they're the same unit and directly comparable (threshold HR is
// necessarily below max HR), whereas pace+HR together only works via two separate axes. Distinct
// tones needed since both would otherwise render in the same "hr" hue -- same precedent
// HealthPage.tsx's own Max+Resting heart rate chart already established (its Resting line takes
// toneColor("pace") for the same reason).
const HR_SERIES: TrendSeries[] = [
  { key: "max_hr_bpm", label: "Max heart rate", color: toneColor("hr"), formatValue: formatBpm },
  {
    key: "threshold_hr_bpm",
    label: "Threshold HR",
    color: toneColor("pace"),
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
    key: "threshold-pace",
    title: "Threshold pace",
    keys: ["threshold_pace_s_per_km"],
    series: THRESHOLD_PACE_SERIES,
  },
  {
    key: "hr",
    title: "Threshold & max HR",
    keys: ["threshold_hr_bpm", "max_hr_bpm"],
    series: HR_SERIES,
  },
];

export function ThresholdMaxHrChart() {
  const [resolution, setResolution] = useState<Resolution>("week");
  const [anchor, setAnchor] = useState(TODAY);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);

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

  const window = useMemo(
    () => computeWindow(resolution, anchor, dataStart, TODAY),
    [resolution, anchor, dataStart],
  );

  function changeResolution(next: Resolution) {
    setResolution(next);
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
      {performance.isError && <p role="alert">Could not load threshold/max HR data.</p>}

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
            />
          }
        />
      )}
    </>
  );
}
