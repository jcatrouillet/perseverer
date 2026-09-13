// Insights' "VO2max" tab -- this project's own independently-computed VO2max, never Garmin's
// precomputed value. `rolling_vdot` (performance_daily_rollup, see performance_rollup.py's own
// docstring) is a 42-day trailing maximum of VDOT under the Daniels-Gilbert model (Daniels &
// Gilbert, "Oxygen Power", 1979) -- under that model VDOT already *is* the VO2max estimate, in
// the same ml/kg/min units clinical VO2max is measured in, not a separate figure needing its own
// conversion. Only one metric exists here, unlike RacePredictionsChart.tsx/ThresholdAnalysisChart.tsx
// (4 races; threshold pace + threshold/max HR), so this skips MetricExplorer's list+detail shell
// entirely -- a picker with nothing to pick between would just be clutter -- but reuses the exact
// TrendControls/trendWindow.ts week/month/year/all-time/custom wiring those two (and FitnessPage.tsx
// before them) already established, per the user's own request for the same navigation here.
// See Vo2maxFactorAnalysis.tsx for "which activities contributed, what's missing", rendered
// alongside this chart on the same tab (InsightsPage.tsx).
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
import { TrendChart, type TrendSeries } from "./TrendChart";
import { TrendControls } from "./TrendControls";

const TODAY = isoDate(new Date());
const VO2MAX_KEY = "vo2max";

function performanceToDailyPoints(rows: PerformanceDailyRollupOut[]): DailyPoint[] {
  return rows.map((r) => ({ local_date: r.local_date, [VO2MAX_KEY]: r.rolling_vdot }));
}

const VO2MAX_SERIES: TrendSeries[] = [
  {
    key: VO2MAX_KEY,
    label: "VO2max",
    color: toneColor("cadence"),
    formatValue: (v) => `${v.toFixed(1)} ml/kg/min`,
  },
];

export function Vo2maxChart() {
  const [resolution, setResolution] = useState<Resolution>("week");
  const [anchor, setAnchor] = useState(TODAY);
  const [customRange, setCustomRange] = useState<CustomRange | null>(null);

  const performance = usePerformance(EARLIEST_PLAUSIBLE_DATE, TODAY);
  const points = useMemo(
    () => performanceToDailyPoints(performance.data ?? []),
    [performance.data],
  );
  const hasData = points.some((p) => typeof p[VO2MAX_KEY] === "number");

  const dataStart = useMemo(() => earliestDateForKeys(points, [VO2MAX_KEY]), [points]);
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

  if (performance.isLoading) return <LoadingSpinner />;
  if (performance.isError) return <p role="alert">Could not load VO2max.</p>;
  if (!hasData) return null;

  const bucketed = bucketSeriesToWindow(points, [VO2MAX_KEY], window);

  return (
    <section className="card">
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
      <ChartFullscreen as="h2" title="VO2max">
        <p className="chart-note">
          A trailing-maximum VDOT (Daniels &amp; Gilbert, 1979) computed from your own race-effort
          runs, in the same ml/kg/min units clinical VO2max is measured in -- not Garmin's own
          precomputed value. See "What's driving this value" below for exactly which run and
          window set today's number.
        </p>
        <TrendChart points={bucketed} series={VO2MAX_SERIES} />
      </ChartFullscreen>
    </section>
  );
}
