// Multi-panel per-second activity charts (Milestone C of docs/adr/0010-phase-6.1-frontend-
// design.md's plan) -- replaces StreamChart.tsx's single-channel-with-a-selector approach with
// one small chart per channel the activity actually has, all sharing one Recharts `syncId` so
// hovering any panel moves a synced cursor/tooltip across all of them at once. Lap boundaries
// draw as vertical reference lines using each lap's own start time, converted to the same
// elapsed-seconds x-axis the stream panels use.
//
// Only a channel the activity actually recorded gets a panel -- there is no fixed six-panel
// layout with empty slots for a treadmill run with no GPS speed, or a run with no power meter.
import {
  Area,
  AreaChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ActivityWorkoutOut, LapOut, StreamResponse } from "../api/types";
import type { IconName } from "./Icon";
import { gapSeriesMinPerKm } from "../gap";
import { toneColor, type Tone } from "../metricStyle";
import {
  buildPauseCompressor,
  detectPauseGaps,
  formatClockDuration,
  isPaceSport,
  rejectSpeedOutliers,
  streamSpeedValue,
} from "../runningStats";
import { expandWorkoutSteps, targetPaceRangeMinPerKm } from "../workoutSteps";
import { Icon } from "./Icon";

interface Panel {
  key: string;
  title: string;
  icon: IconName;
  tone: Tone;
  kind: "area" | "line";
  unit: string;
  transform: (raw: number | null) => number | null;
  formatValue: (v: number) => string;
}

function panelsFor(sport: string): Panel[] {
  const paceSport = isPaceSport(sport);
  const panels: Panel[] = [
    {
      key: "altitude_m",
      title: "Elevation",
      icon: "mountain",
      tone: "elevation",
      kind: "area",
      unit: "m",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)} m`,
    },
    {
      key: "speed_mps",
      title: paceSport ? "Pace" : "Speed",
      icon: "gauge",
      tone: "pace",
      kind: "line",
      unit: paceSport ? "/km" : "km/h",
      transform: (v) => streamSpeedValue(sport, v),
      formatValue: (v) => (paceSport ? `${formatPace(v)} /km` : `${v.toFixed(1)} km/h`),
    },
  ];
  // GAP is a running-specific concept (gap.ts's own docstring) -- right after Pace, matching
  // where the user asked for it. Its values live on a synthetic "gap" channel this component
  // computes itself (see the `series` augmentation below), not a raw stream channel, since GAP
  // is derived from distance_m + altitude_m + speed_mps together rather than read off one.
  if (paceSport) {
    panels.push({
      key: "gap",
      title: "Grade Adjusted Pace",
      icon: "trend",
      tone: "load",
      kind: "line",
      unit: "/km",
      transform: (v) => v,
      formatValue: (v) => `${formatPace(v)} /km`,
    });
  }
  panels.push(
    {
      key: "heart_rate",
      title: "Heart rate",
      icon: "heart",
      tone: "hr",
      kind: "line",
      unit: "bpm",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)} bpm`,
    },
    {
      key: "respiration_rate",
      title: "Respiration",
      icon: "pulse",
      tone: "cadence",
      kind: "line",
      unit: "brpm",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)} brpm`,
    },
    {
      key: "cadence",
      title: "Cadence",
      icon: "steps",
      tone: "cadence",
      kind: "line",
      unit: "spm",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)} spm`,
    },
    {
      key: "power",
      title: "Power",
      icon: "bolt",
      tone: "power",
      kind: "line",
      unit: "W",
      transform: (v) => v,
      formatValue: (v) => `${v.toFixed(0)} W`,
    },
  );
  return panels;
}

function formatPace(minPerKm: number): string {
  const min = Math.floor(minPerKm);
  const sec = Math.round((minPerKm - min) * 60);
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

/** A custom Tooltip renderer scoped to a single panel's own `v` series -- the workout-target
 * overlay's own `target` dataKey (a [low, high] pair, not a plain number) rides on the same
 * shared chart `data` and would otherwise show up in Recharts' default tooltip too: formatting a
 * tuple through `panel.formatValue` produces a garbage "NaN:NaN" row, and even with a formatter
 * fix it's a background reference visual (like the lap bands or the expected-pace line, neither
 * of which show up in the tooltip either), not a value this panel should report on hover. */
function panelTooltipContent(panel: Panel) {
  return function PanelTooltip({
    active,
    payload,
    label,
  }: {
    active?: boolean;
    payload?: readonly { dataKey?: unknown; value?: unknown }[];
    label?: number | string;
  }) {
    if (!active || !payload) return null;
    const entry = payload.find((p) => p.dataKey === "v");
    const value = entry?.value;
    if (typeof value !== "number") return null;
    return (
      <div
        style={{
          background: "var(--color-surface-raised)",
          border: "1px solid var(--color-border)",
          borderRadius: 4,
          padding: "6px 10px",
          fontSize: 12,
        }}
      >
        <div>{formatClockDuration(Number(label))}</div>
        <div>
          {panel.title}: {panel.formatValue(value)}
        </div>
      </div>
    );
  };
}

export function ActivityCharts({
  stream,
  laps,
  sport,
  distanceM,
  durationS,
  highlightLapIndex,
  workout,
}: {
  stream: StreamResponse;
  laps: LapOut[];
  sport: string;
  /** The activity's own overall distance/duration -- used to draw a flat "expected pace"
   * reference line on the Pace panel at the activity's own average pace, distinct from the
   * per-second (noisy) pace series itself. Optional: omitted (e.g. by existing tests that don't
   * care about it) simply skips the line, same as any other panel that has nothing to show. */
  distanceM?: number | null;
  durationS?: number | null;
  /** Index into `laps` to highlight across every panel -- driven by hovering the Intervals
   * table's own rows on ActivityDetailPage, mirroring SplitsTable/ActivityRouteMap's existing
   * hover-highlight pattern for per-km splits. */
  highlightLapIndex?: number | null;
  /** The pre-planned workout structure, if this activity has one -- rendered as target pace
   * bands on the Pace panel, aligned to `laps` by position: the device creates one lap per
   * executed workout step (confirmed against a real structured-workout FIT file), so expanded
   * step i's target range applies to lap i's own actual time range. Omitted/null skips the
   * overlay entirely, same as any other optional panel feature. */
  workout?: ActivityWorkoutOut | null;
}) {
  if (stream.timestamps.length === 0) {
    return <p>No stream data available.</p>;
  }

  const startMs = new Date(stream.timestamps[0]!).getTime();
  const rawElapsed = stream.timestamps.map((t) => (new Date(t).getTime() - startMs) / 1000);
  // A device pause (watch stopped/paused mid-activity) shows up as a big gap between two
  // consecutive recorded samples. Compressing it out of the x-axis -- rather than drawing a
  // straight line across dead time nothing was recorded for -- is what "moving time" already
  // means everywhere else in this app (runningStats.ts::effectiveDurationS); the per-second
  // charts should read the same way. `compress` is applied to both the stream samples below and
  // to lap start times just after, so lap boundaries/workout bands stay aligned with the now-
  // compressed stream.
  const pauseDetection = detectPauseGaps(rawElapsed);
  const compress = buildPauseCompressor(rawElapsed, pauseDetection);
  const elapsed = rawElapsed.map(compress);
  const maxT = elapsed[elapsed.length - 1] ?? 0;

  // Lap starts after the activity's own start, converted to the same elapsed-seconds axis the
  // stream panels use -- the first lap's own start essentially coincides with t=0, so it's
  // skipped as a redundant line sitting on top of the y-axis.
  const lapMarks = laps
    .map((lap) => compress((new Date(lap.start_time_utc).getTime() - startMs) / 1000))
    .filter((t) => t > 1 && t < maxT);

  // "Segments on top of the graph": each lap shaded as an alternating-tint background band,
  // built from the same boundaries as the lapMarks lines above plus the activity's own start/
  // end as the outer edges -- a lap is a *range*, the ReferenceLines only mark where one ends
  // and the next begins.
  const lapBoundaries = [0, ...lapMarks, maxT];
  const lapBands = lapBoundaries.slice(0, -1).map((start, i) => ({
    start,
    end: lapBoundaries[i + 1]!,
    shaded: i % 2 === 1,
  }));

  // The planned workout's target pace per step, positioned onto the same elapsed-seconds
  // boundaries as lapBands -- zipped by position with `laps` (not by any id/time match), since
  // a workout step has no timestamp of its own, only the recorded lap it produced does. Zipped
  // only up to whichever list is shorter: a device's own trailing "stop" lap (confirmed real --
  // 13 recorded laps for 12 expanded steps on a real file) has no corresponding step, and an
  // activity stopped early could in principle have fewer laps than planned steps. `slowMinPerKm`
  // (the target range's slower/higher-number bound) is the overlay's threshold line: at or
  // faster than it is shaded grey, slower than it is left white -- see the rendering below for
  // why only one edge, not the full range, is drawn.
  const workoutBands =
    workout != null
      ? expandWorkoutSteps(workout.steps)
          .map((step, i) => {
            const range = targetPaceRangeMinPerKm(step, sport);
            if (range == null || i >= lapBoundaries.length - 1) return null;
            return { start: lapBoundaries[i]!, end: lapBoundaries[i + 1]!, slowMinPerKm: range[1] };
          })
          .filter(
            (b): b is { start: number; end: number; slowMinPerKm: number } => b != null,
          )
      : [];

  // The hovered Intervals-table row's own time range, in the same elapsed-seconds terms as
  // lapBands above -- `laps` and `lapBoundaries` are built from the same ordered list, so the
  // lap at `highlightLapIndex` maps directly onto boundary index `highlightLapIndex`/`+1`.
  const highlightRange: [number, number] | null =
    highlightLapIndex != null && highlightLapIndex >= 0 && highlightLapIndex < lapBoundaries.length - 1
      ? [lapBoundaries[highlightLapIndex]!, lapBoundaries[highlightLapIndex + 1]!]
      : null;

  // "Expected pace": a flat reference line at the activity's own overall average pace (not a
  // per-lap or per-second value) -- the simplest, clarified-via-AskUserQuestion reading of the
  // request. Only meaningful on the Pace panel of a foot sport with a real distance/duration.
  const expectedPaceMinPerKm =
    isPaceSport(sport) && distanceM != null && distanceM > 0 && durationS != null && durationS > 0
      ? durationS / 60 / (distanceM / 1000)
      : null;

  // The very first sample recorded right after a device pause is a resume artifact (GPS
  // reacquisition / stride restart -- the same category as the very first sample of the whole
  // activity), not a real momentary pace, so it's nulled outright rather than left to
  // rejectSpeedOutliers below: its own neighbors are often *also* still-ramping-up samples,
  // which can fool a neighbor-agreement check into treating it as normal (confirmed against a
  // real paused lap: three post-resume samples in a row all read as a slow, mutually-consistent
  // cluster). Isolated single-point glitches elsewhere are nulled out here too, before pace
  // conversion -- see rejectSpeedOutliers' own docstring. Feeds both the Pace/Speed panel (via
  // `series.speed_mps` below) and GAP (via `paceSeries`), so neither shows a glitch the other
  // doesn't.
  const postPauseIndices = new Set(pauseDetection.gaps.map((g) => g.postGapIndex));
  const speedWithPausesMarked = stream.series.speed_mps?.map((v, i) =>
    postPauseIndices.has(i) ? null : v,
  );
  const cleanedSpeed = speedWithPausesMarked != null ? rejectSpeedOutliers(speedWithPausesMarked) : undefined;

  // GAP isn't a raw stream channel -- it's derived from distance_m + altitude_m + the same
  // speed_mps->pace conversion the Pace panel already does -- so it's computed once here onto a
  // synthetic "gap" channel, letting the rest of this component treat it like any other panel
  // (including the panel-presence filter below, which naturally drops it when there's no
  // altitude data to derive a grade from).
  const paceSeries = cleanedSpeed?.map((v) => streamSpeedValue(sport, v)) ?? [];
  const gapSeries =
    isPaceSport(sport) && cleanedSpeed != null && stream.series.distance_m != null && stream.series.altitude_m != null
      ? gapSeriesMinPerKm(stream.series.distance_m, stream.series.altitude_m, paceSeries)
      : null;
  const series: Record<string, (number | null)[]> = {
    ...stream.series,
    ...(cleanedSpeed != null ? { speed_mps: cleanedSpeed } : {}),
    ...(gapSeries != null ? { gap: gapSeries } : {}),
  };

  const panels = panelsFor(sport).filter((panel) => {
    const raw = series[panel.key];
    return raw != null && raw.some((v) => panel.transform(v) != null);
  });

  if (panels.length === 0) {
    return <p>No stream data available.</p>;
  }

  return (
    <div className="activity-charts">
      {panels.map((panel, panelIndex) => {
        const data = elapsed.map((t, i) => ({
          t,
          v: panel.transform(series[panel.key]?.[i] ?? null),
        }));
        // The workout-target step overlay, only for the Pace panel: baseline is the panel's own
        // visible min (the fastest recorded pace, matching what Recharts' "auto" Y domain would
        // already settle on as the top edge), so the grey area fills from the top of the chart
        // down to the target step's slower threshold -- grey for running at or faster than the
        // prescribed pace, left uncoloured (white) for running slower than it.
        let chartData: { t: number; v: number | null; target?: [number, number] | null }[] = data;
        if (panel.key === "speed_mps" && workoutBands.length > 0) {
          const values = data.map((d) => d.v).filter((v): v is number => v != null);
          const domainMin = Math.min(...values);
          chartData = data.map((d) => {
            const band = workoutBands.find((b) => d.t >= b.start && d.t < b.end);
            return { ...d, target: band ? [domainMin, band.slowMinPerKm] : null };
          });
        }
        const color = toneColor(panel.tone);
        // Only the bottom-most panel shows time-axis ticks/labels -- every panel already shares
        // one x-axis via `syncId`, so repeating the same tick row under each one is pure
        // vertical padding a compact stacked layout doesn't need.
        const isLastPanel = panelIndex === panels.length - 1;
        return (
          <div className="activity-charts__panel" key={panel.key}>
            <h4>
              <span className={`icon-chip tone-${panel.tone}`}>
                <Icon name={panel.icon} />
              </span>
              {panel.title}
            </h4>
            <ResponsiveContainer width="100%" height={92}>
              {panel.kind === "area" ? (
                <AreaChart data={data} syncId="activity-charts" margin={{ top: 2, right: 12, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id={`activity-chart-fill-${panel.key}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={color} stopOpacity={0.32} />
                      <stop offset="100%" stopColor={color} stopOpacity={0.03} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="t"
                    type="number"
                    domain={[0, "dataMax"]}
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    tickFormatter={formatClockDuration}
                    hide={!isLastPanel}
                  />
                  <YAxis stroke="var(--color-text-muted)" fontSize={11} width={40} domain={["auto", "auto"]} />
                  {lapBands
                    .filter((b) => b.shaded)
                    .map((b) => (
                      <ReferenceArea
                        key={b.start}
                        x1={b.start}
                        x2={b.end}
                        fill="var(--color-text-faint)"
                        fillOpacity={0.08}
                        stroke="none"
                        ifOverflow="visible"
                      />
                    ))}
                  {highlightRange && (
                    <ReferenceArea
                      x1={highlightRange[0]}
                      x2={highlightRange[1]}
                      fill="var(--color-load)"
                      fillOpacity={0.18}
                      stroke="var(--color-load)"
                      strokeOpacity={0.5}
                      ifOverflow="visible"
                    />
                  )}
                  {lapMarks.map((t) => (
                    <ReferenceLine key={t} x={t} stroke="var(--color-text-faint)" strokeDasharray="2 2" />
                  ))}
                  <Tooltip content={panelTooltipContent(panel)} />
                  <Area
                    type="monotone"
                    dataKey="v"
                    stroke={color}
                    fill={`url(#activity-chart-fill-${panel.key})`}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls
                  />
                </AreaChart>
              ) : (
                <ComposedChart data={chartData} syncId="activity-charts" margin={{ top: 2, right: 12, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="t"
                    type="number"
                    domain={[0, "dataMax"]}
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    tickFormatter={formatClockDuration}
                    hide={!isLastPanel}
                  />
                  <YAxis stroke="var(--color-text-muted)" fontSize={11} width={40} domain={["auto", "auto"]} />
                  {lapBands
                    .filter((b) => b.shaded)
                    .map((b) => (
                      <ReferenceArea
                        key={b.start}
                        x1={b.start}
                        x2={b.end}
                        fill="var(--color-text-faint)"
                        fillOpacity={0.08}
                        stroke="none"
                        ifOverflow="visible"
                      />
                    ))}
                  {/* The planned workout's target pace, shown as a grey step area filled down to
                      the chart's bottom edge -- matching Garmin Connect's own rendering (no
                      on-chart text: the exact numbers are already in the workout panel above). */}
                  {panel.key === "speed_mps" && workoutBands.length > 0 && (
                    <Area
                      type="stepAfter"
                      dataKey="target"
                      stroke="none"
                      fill="var(--color-text-faint)"
                      fillOpacity={0.35}
                      isAnimationActive={false}
                      connectNulls={false}
                    />
                  )}
                  {highlightRange && (
                    <ReferenceArea
                      x1={highlightRange[0]}
                      x2={highlightRange[1]}
                      fill="var(--color-load)"
                      fillOpacity={0.18}
                      stroke="var(--color-load)"
                      strokeOpacity={0.5}
                      ifOverflow="visible"
                    />
                  )}
                  {lapMarks.map((t) => (
                    <ReferenceLine key={t} x={t} stroke="var(--color-text-faint)" strokeDasharray="2 2" />
                  ))}
                  {panel.key === "speed_mps" && expectedPaceMinPerKm != null && (
                    <ReferenceLine
                      y={expectedPaceMinPerKm}
                      stroke="var(--color-text-muted)"
                      strokeDasharray="4 4"
                      label={{ value: "Avg", position: "insideTopRight", fill: "var(--color-text-muted)", fontSize: 11 }}
                    />
                  )}
                  <Tooltip content={panelTooltipContent(panel)} />
                  <Line
                    type="monotone"
                    dataKey="v"
                    stroke={color}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls
                  />
                </ComposedChart>
              )}
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}
