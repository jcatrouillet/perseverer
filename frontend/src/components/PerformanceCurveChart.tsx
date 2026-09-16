// Insights' "Performance Curve" tab: the best *sustained* value for each of a fixed set of
// durations (1s-2h), across every qualifying activity in the chosen date range -- the same idea
// as cycling's "critical power curve" or Runalyze's own "Heart Rate Curve." See
// performance_curve.py's own module docstring for the full model (the sliding-window search,
// gap disqualification, and why the athlete's own already-computed threshold pace/HR are shown
// alongside as reference lines, never blended into the curve itself).
//
// pace/GAP are always running-only (matching every other pace feature in this app); heart_rate
// instead offers a sport checklist, built from the athlete's own real sports (never a hardcoded
// catalog -- same "derive from real data" precedent buildMarkerCatalog established for blood-test
// markers), default all checked.
import { useMemo, useState } from "react";
import {
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";

import { useAllActivities, usePerformanceCurve } from "../api/queries";
import type { PerformanceCurvePointOut } from "../api/types";
import { toneColor } from "../metricStyle";
import { formatMinPerKm } from "../runningStats";
import { ChartFullscreen } from "./ChartFullscreen";
import { LoadingSpinner } from "./LoadingSpinner";
import { StatTile } from "./StatTile";

type Metric = "pace" | "gap" | "heart_rate";

const RANGE_PRESETS = [
  { key: "3m", label: "Last 3 months", months: 3 },
  { key: "6m", label: "Last 6 months", months: 6 },
  { key: "1y", label: "Last year", months: 12 },
  { key: "all", label: "All time", months: null },
] as const;
type RangeKey = (typeof RANGE_PRESETS)[number]["key"];

function rangeFor(key: RangeKey, earliestDate: string | undefined): { start: string; end: string } {
  const today = new Date();
  const end = today.toISOString().slice(0, 10);
  const preset = RANGE_PRESETS.find((p) => p.key === key)!;
  if (preset.months === null) {
    return { start: earliestDate ?? "2015-01-01", end };
  }
  const start = new Date(today);
  start.setMonth(start.getMonth() - preset.months);
  return { start: start.toISOString().slice(0, 10), end };
}

// A superset of the reference product's own shown labels -- see performance_curve.py's own
// DURATION_BUCKETS_S, kept in sync by eye (not imported -- this is presentation-only tick
// labeling, the actual bucket set always comes from the API response itself).
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) {
    const m = seconds / 60;
    return Number.isInteger(m) ? `${m}m` : `${(seconds / 60).toFixed(1)}m`;
  }
  const h = seconds / 3600;
  return Number.isInteger(h) ? `${h}h` : `${h.toFixed(1)}h`;
}

function formatValue(metric: Metric, value: number): string {
  if (metric === "heart_rate") return `${Math.round(value)} bpm`;
  return `${formatMinPerKm(value / 60)} /km`;
}

function CurveTooltip({ active, payload, metric }: TooltipContentProps & { metric: Metric }) {
  if (!active || !payload?.length) return null;
  const point = payload[0]!.payload as PerformanceCurvePointOut;
  return (
    <div
      style={{
        background: "var(--color-surface-raised)",
        border: "1px solid var(--color-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 12px",
      }}
    >
      <p style={{ margin: 0, fontWeight: 600 }}>{formatDuration(point.duration_s)}</p>
      <p style={{ margin: 0 }}>{formatValue(metric, point.value)}</p>
      <p style={{ margin: 0, color: "var(--color-text-muted)" }}>{point.local_date}</p>
    </div>
  );
}

function pointAt(points: PerformanceCurvePointOut[], durationS: number): PerformanceCurvePointOut | undefined {
  return points.find((p) => p.duration_s === durationS);
}

export function PerformanceCurveChart() {
  const [metric, setMetric] = useState<Metric>("pace");
  const [rangeKey, setRangeKey] = useState<RangeKey>("3m");
  const [selectedSports, setSelectedSports] = useState<Set<string> | null>(null);

  // Only actually needed to build the heart-rate sport checklist, but cheap and cached
  // (react-query) once fetched -- the same "fetch full history once, aggregate client-side"
  // precedent Eddington/personal-records already establish.
  const allActivities = useAllActivities({});
  const knownSports = useMemo(
    () => Array.from(new Set((allActivities.data ?? []).map((a) => a.sport))).sort(),
    [allActivities.data],
  );
  const effectiveSports = selectedSports ?? new Set(knownSports);

  const earliestDate = useMemo(() => {
    const dates = (allActivities.data ?? []).map((a) => a.local_date).filter((d): d is string => !!d);
    return dates.length > 0 ? dates.reduce((a, b) => (a < b ? a : b)) : undefined;
  }, [allActivities.data]);

  const { start, end } = rangeFor(rangeKey, earliestDate);
  const curve = usePerformanceCurve(
    metric,
    start,
    end,
    metric === "heart_rate" ? Array.from(effectiveSports) : undefined,
  );

  function toggleSport(sport: string) {
    const next = new Set(effectiveSports);
    if (next.has(sport)) next.delete(sport);
    else next.add(sport);
    setSelectedSports(next);
  }

  const color = toneColor(metric === "heart_rate" ? "hr" : metric === "gap" ? "cadence" : "pace");

  return (
    <section className="card">
      <h2>Performance Curve</h2>
      <p className="chart-note">
        The best sustained value for each duration, across every activity in range -- not one
        activity's own average, the single best window anywhere. Dashed lines (when shown) are
        your own already-computed threshold pace/HR, for comparison -- never blended into the
        curve itself.
      </p>

      <div className="insights-tabs" role="tablist" style={{ marginBottom: "var(--space-3)" }}>
        <button
          type="button"
          role="tab"
          aria-selected={metric === "pace"}
          className={metric === "pace" ? "is-active" : undefined}
          onClick={() => setMetric("pace")}
        >
          Pace
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={metric === "gap"}
          className={metric === "gap" ? "is-active" : undefined}
          onClick={() => setMetric("gap")}
        >
          GAP
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={metric === "heart_rate"}
          className={metric === "heart_rate" ? "is-active" : undefined}
          onClick={() => setMetric("heart_rate")}
        >
          Heart rate
        </button>
      </div>

      <label className="field" style={{ maxWidth: "16rem" }}>
        Time range
        <select
          className="input"
          value={rangeKey}
          onChange={(e) => setRangeKey(e.target.value as RangeKey)}
        >
          {RANGE_PRESETS.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label}
            </option>
          ))}
        </select>
      </label>

      {metric === "heart_rate" && knownSports.length > 0 && (
        <fieldset className="field" style={{ border: "none", padding: 0 }}>
          <legend>Sports included</legend>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
            {knownSports.map((sport) => (
              <label key={sport} style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                <input
                  type="checkbox"
                  checked={effectiveSports.has(sport)}
                  onChange={() => toggleSport(sport)}
                />
                {sport.replace(/_/g, " ")}
              </label>
            ))}
          </div>
        </fieldset>
      )}

      {curve.isLoading && <LoadingSpinner />}
      {curve.isError && <p role="alert">Could not load the performance curve.</p>}
      {curve.data && !curve.data.available && (
        <p className="chart-note">No qualifying activity in this range yet.</p>
      )}
      {curve.data?.available && (
        <>
          <div className="stat-grid">
            {(() => {
              const p20 = pointAt(curve.data.points, 1200);
              return p20 ? (
                <StatTile
                  label="20-min best"
                  value={formatValue(metric, p20.value)}
                  icon={metric === "heart_rate" ? "heart" : "gauge"}
                  tone={metric === "heart_rate" ? "hr" : "pace"}
                  hero
                />
              ) : null;
            })()}
            {(() => {
              const p60 = pointAt(curve.data.points, 3600);
              return p60 ? (
                <StatTile
                  label="60-min best"
                  value={formatValue(metric, p60.value)}
                  icon={metric === "heart_rate" ? "heart" : "gauge"}
                  tone={metric === "heart_rate" ? "hr" : "pace"}
                  hero
                />
              ) : null;
            })()}
          </div>
          {(() => {
            const p60 = pointAt(curve.data.points, 3600);
            const threshold =
              metric === "heart_rate" ? curve.data.threshold_hr_bpm : curve.data.threshold_pace_s_per_km;
            if (!p60 || threshold == null) return null;
            if (metric === "heart_rate") {
              const diff = p60.value - threshold;
              const verb = diff > 0 ? "higher than" : diff < 0 ? "lower than" : "equal to";
              return (
                <p className="chart-note">
                  Your 60-min best is {formatValue(metric, Math.abs(diff))} {verb} your computed
                  threshold heart rate ({formatValue(metric, threshold)}).
                </p>
              );
            }
            const diffS = threshold - p60.value;
            const verb = diffS > 0 ? "faster than" : diffS < 0 ? "slower than" : "equal to";
            return (
              <p className="chart-note">
                Your 60-min best is {formatValue(metric, Math.abs(diffS))} {verb} your computed
                threshold pace ({formatValue(metric, threshold)}).
              </p>
            );
          })()}

          <ChartFullscreen as="h3" title={`${metric === "heart_rate" ? "Heart rate" : metric === "gap" ? "GAP" : "Pace"} curve`}>
            <ResponsiveContainer width="100%" height={320}>
              <ComposedChart
                data={curve.data.points}
                margin={{ top: 8, right: 16, bottom: 0, left: 0 }}
              >
                <XAxis
                  dataKey="duration_s"
                  type="number"
                  scale="log"
                  domain={["auto", "auto"]}
                  ticks={curve.data.points.map((p) => p.duration_s)}
                  tickFormatter={formatDuration}
                  stroke="var(--color-text-muted)"
                  fontSize={11}
                />
                <YAxis
                  dataKey="value"
                  type="number"
                  domain={["auto", "auto"]}
                  reversed={metric !== "heart_rate"}
                  tickFormatter={(v: number) => formatValue(metric, v)}
                  stroke="var(--color-text-muted)"
                  fontSize={11}
                  width={70}
                />
                <Tooltip content={(props) => <CurveTooltip {...props} metric={metric} />} />
                {metric === "heart_rate" ? (
                  <>
                    {curve.data.max_hr_bpm != null && (
                      <ReferenceLine
                        y={curve.data.max_hr_bpm}
                        ifOverflow="extendDomain"
                        stroke="var(--color-text-faint)"
                        strokeDasharray="4 4"
                      />
                    )}
                    {curve.data.threshold_hr_bpm != null && (
                      <ReferenceLine
                        y={curve.data.threshold_hr_bpm}
                        ifOverflow="extendDomain"
                        stroke="var(--color-text-faint)"
                        strokeDasharray="4 4"
                      />
                    )}
                    {curve.data.aerobic_threshold_hr_bpm != null && (
                      <ReferenceLine
                        y={curve.data.aerobic_threshold_hr_bpm}
                        ifOverflow="extendDomain"
                        stroke="var(--color-text-faint)"
                        strokeDasharray="4 4"
                      />
                    )}
                  </>
                ) : (
                  <>
                    {curve.data.threshold_pace_s_per_km != null && (
                      <ReferenceLine
                        y={curve.data.threshold_pace_s_per_km}
                        ifOverflow="extendDomain"
                        stroke="var(--color-text-faint)"
                        strokeDasharray="4 4"
                      />
                    )}
                    {curve.data.aerobic_threshold_pace_s_per_km != null && (
                      <ReferenceLine
                        y={curve.data.aerobic_threshold_pace_s_per_km}
                        ifOverflow="extendDomain"
                        stroke="var(--color-text-faint)"
                        strokeDasharray="4 4"
                      />
                    )}
                  </>
                )}
                <Line
                  isAnimationActive={false}
                  type="monotone"
                  dataKey="value"
                  stroke={color}
                  strokeWidth={2}
                  dot={{ r: 3, fill: color, strokeWidth: 0 }}
                  activeDot={{ r: 5 }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </ChartFullscreen>
        </>
      )}
    </section>
  );
}
