// Race-time predictions (5k/10k/half marathon/marathon), independently computed from the
// athlete's own rolling VDOT -- see performance_rollup.py's own docstring for the model and why
// it deliberately never uses Garmin's own daily_race_predictions. One metric per distance, same
// MetricExplorer + TrendControls + trendWindow.ts wiring as FitnessPage.tsx.
//
// Deliberately 4 separate single-series charts, not one combined chart: the 4 distances span a
// ~10x time range (roughly 20 minutes to 5 hours), which would flatten the 5k/10k lines to
// near-invisibility on one shared y-axis, and TrendChart only supports two axes (left/right)
// anyway -- nowhere near the four this would need.
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
    predicted_5k_s: r.predicted_5k_s,
    predicted_10k_s: r.predicted_10k_s,
    predicted_half_marathon_s: r.predicted_half_marathon_s,
    predicted_marathon_s: r.predicted_marathon_s,
  }));
}

/** "M:SS" under an hour, "H:MM:SS" at or above -- matches sharing.py's own `_format_duration`
 * convention for consistency across the codebase. */
export function formatRaceTime(totalSeconds: number): string {
  const s = Math.round(totalSeconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${m}:${String(sec).padStart(2, "0")}`;
}

interface RaceDef {
  key: string;
  title: string;
  seriesKey: string;
}

const RACE_DEFS: RaceDef[] = [
  { key: "5k", title: "5K", seriesKey: "predicted_5k_s" },
  { key: "10k", title: "10K", seriesKey: "predicted_10k_s" },
  { key: "half-marathon", title: "Half marathon", seriesKey: "predicted_half_marathon_s" },
  { key: "marathon", title: "Marathon", seriesKey: "predicted_marathon_s" },
];

export function RacePredictionsChart() {
  const [resolution, setResolution] = useState<Resolution>("week");
  const [anchor, setAnchor] = useState(TODAY);
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);

  const performance = usePerformance(EARLIEST_PLAUSIBLE_DATE, TODAY);

  const points = useMemo(
    () => performanceToDailyPoints(performance.data ?? []),
    [performance.data],
  );

  const availableRaces = useMemo(
    () => RACE_DEFS.filter((def) => hasAnyRawValue(points, [def.seriesKey])),
    [points],
  );
  const activeRace =
    availableRaces.find((d) => d.key === selectedMetric) ?? availableRaces[0] ?? null;

  // Scoped to the *selected* race's own key -- so e.g. Marathon's own "All time" starts where
  // the athlete's VDOT history first makes a marathon prediction possible, same reasoning
  // FitnessPage.tsx's own per-metric earliestDateForKeys already established.
  const dataStart = useMemo(
    () => (activeRace ? earliestDateForKeys(points, [activeRace.seriesKey]) : TODAY),
    [activeRace, points],
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
      availableRaces.map((def) => {
        const series: TrendSeries[] = [
          {
            key: def.seriesKey,
            label: def.title,
            color: toneColor("pace"),
            formatValue: formatRaceTime,
          },
        ];
        const bucketed = bucketSeriesToWindow(points, [def.seriesKey], window);
        return {
          key: def.key,
          title: def.title,
          content: (
            <ChartFullscreen title={def.title}>
              <TrendChart points={bucketed} series={series} />
            </ChartFullscreen>
          ),
        };
      }),
    [availableRaces, points, window],
  );

  return (
    <>
      {performance.isLoading && <LoadingSpinner />}
      {performance.isError && <p role="alert">Could not load race predictions.</p>}

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
