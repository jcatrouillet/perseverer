// The run comparison's charts: one panel per channel (pace, GAP, heart rate, cadence, elevation,
// power), each run drawn in its own colour slot, every run resampled onto a shared distance (or
// moving-time) axis by compareRuns.ts::alignRuns. All panels share a Recharts `syncId`, so hovering
// one moves the cursor on all of them, and the tooltip lists every run's value at that point.
// Pace and GAP axes are reversed (faster on top), as on the activity page. A channel no compared
// run recorded (power without a power meter) gets no panel.
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  alignRuns,
  channelHasData,
  compareColor,
  type CompareAxis,
  COMPARE_SPORTS,
  type CompareChannel,
  type CompareSport,
  type RunSeries,
} from "../compareRuns";
import type { Tone } from "../metricStyle";
import { formatClockDuration } from "../runningStats";
import { ChartFullscreen } from "./ChartFullscreen";
import { Icon, type IconName } from "./Icon";

interface PanelDef {
  channel: CompareChannel;
  title: string;
  icon: IconName;
  tone: Tone;
  reversed?: boolean;
  format: (v: number) => string;
  tick: (v: number) => string;
}

function paceText(minPerKm: number): string {
  let min = Math.floor(minPerKm);
  let sec = Math.round((minPerKm - min) * 60);
  if (sec === 60) {
    min += 1;
    sec = 0;
  }
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

const hr: PanelDef = {
  channel: "heart_rate",
  title: "Heart rate",
  icon: "heart",
  tone: "hr",
  format: (v) => `${v.toFixed(0)} bpm`,
  tick: (v) => v.toFixed(0),
};
const elevation: PanelDef = {
  channel: "altitude",
  title: "Elevation",
  icon: "mountain",
  tone: "elevation",
  format: (v) => `${v.toFixed(0)} m`,
  tick: (v) => v.toFixed(0),
};
const power: PanelDef = {
  channel: "power",
  title: "Power",
  icon: "bolt",
  tone: "power",
  format: (v) => `${v.toFixed(0)} W`,
  tick: (v) => v.toFixed(0),
};

const cadenceFor = (sport: CompareSport): PanelDef => ({
  channel: "cadence",
  title: "Cadence",
  icon: "steps",
  tone: "cadence",
  // A runner's cadence is steps per minute; a rider's is pedal revolutions.
  format: (v) => `${v.toFixed(0)} ${sport === "running" ? "spm" : "rpm"}`,
  tick: (v) => v.toFixed(0),
});

const PACE: PanelDef = {
  channel: "pace",
  title: "Pace",
  icon: "gauge",
  tone: "pace",
  reversed: true,
  format: (v) => `${paceText(v)} /km`,
  tick: paceText,
};
const GAP: PanelDef = {
  channel: "gap",
  title: "Grade Adjusted Pace",
  icon: "trend",
  tone: "load",
  reversed: true,
  format: (v) => `${paceText(v)} /km`,
  tick: paceText,
};
const SPEED: PanelDef = {
  channel: "speed",
  title: "Speed",
  icon: "gauge",
  tone: "ride",
  format: (v) => `${v.toFixed(1)} km/h`,
  tick: (v) => v.toFixed(0),
};

// Each sport's panels in the order it reads best: a run leads with pace, a ride with speed and power.
const PANELS: Record<CompareSport, PanelDef[]> = {
  running: [PACE, GAP, hr, cadenceFor("running"), elevation, power],
  cycling: [SPEED, power, hr, cadenceFor("cycling"), elevation],
};

export interface ComparedRun {
  slot: number;
  label: string;
  series: RunSeries;
}

/** The Y range a pace axis shows: the 2nd to 98th percentile of every run's values, padded a
 * little. A few slow moments (a walk break, a stop the clean-up kept) would otherwise stretch the
 * axis to 20:00 /km and flatten the actual running into a thin band; they run off the edge. */
export function paceDomain(rows: Record<string, number | null>[]): [number, number] | null {
  const values: number[] = [];
  for (const row of rows) {
    for (const [key, v] of Object.entries(row)) {
      if (key !== "x" && typeof v === "number" && Number.isFinite(v)) values.push(v);
    }
  }
  if (values.length < 2) return null;
  values.sort((a, b) => a - b);
  const at = (q: number) => values[Math.min(values.length - 1, Math.floor(q * values.length))]!;
  const low = at(0.02);
  const high = at(0.98);
  const pad = Math.max((high - low) * 0.05, 0.05);
  return [Math.max(0, low - pad), high + pad];
}

function axisLabel(axis: CompareAxis, x: number): string {
  return axis === "distance" ? `${x.toFixed(1)} km` : formatClockDuration(x);
}

function tooltipFor(panel: PanelDef, runs: ComparedRun[], axis: CompareAxis) {
  return function CompareTooltip({
    active,
    payload,
    label,
  }: {
    active?: boolean;
    payload?: readonly { dataKey?: unknown; value?: unknown }[];
    label?: number | string;
  }) {
    if (!active || !payload) return null;
    const values = new Map(payload.map((p) => [String(p.dataKey), p.value]));
    const rows = runs.filter((run) => typeof values.get(`s${run.slot}`) === "number");
    if (rows.length === 0) return null;
    return (
      <div className="compare-tooltip">
        <div className="compare-tooltip__x">{axisLabel(axis, Number(label))}</div>
        {rows.map((run) => (
          <div key={run.slot} className="compare-tooltip__row">
            <span className="compare-swatch" style={{ background: compareColor(run.slot) }} />
            <span className="compare-tooltip__label">{run.label}</span>
            <span className="compare-tooltip__value">
              {panel.format(values.get(`s${run.slot}`) as number)}
            </span>
          </div>
        ))}
      </div>
    );
  };
}

export function CompareCharts({
  runs,
  axis,
  sport = "running",
}: {
  runs: ComparedRun[];
  axis: CompareAxis;
  sport?: CompareSport;
}) {
  const panels = PANELS[sport].filter((panel) => channelHasData(runs, panel.channel));
  if (panels.length === 0) return <p>None of these {COMPARE_SPORTS[sport].many} has chart data.</p>;

  return (
    <div className="compare-charts">
      {panels.map((panel, index) => {
        const data = alignRuns(runs, panel.channel, axis);
        const isLast = index === panels.length - 1;
        const domain = panel.reversed ? paceDomain(data) : null;
        return (
          <div className="compare-charts__panel" key={panel.channel}>
            <ChartFullscreen
              as="h3"
              title={panel.title}
              titlePrefix={
                <span className={`icon-chip tone-${panel.tone}`}>
                  <Icon name={panel.icon} />
                </span>
              }
            >
              <ResponsiveContainer width="100%" height={150}>
                <LineChart
                  data={data}
                  syncId="compare-runs"
                  margin={{ top: 4, right: 12, bottom: 0, left: 0 }}
                >
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="x"
                    type="number"
                    domain={[0, "dataMax"]}
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    tickFormatter={(x: number) => axisLabel(axis, x)}
                    hide={!isLast}
                  />
                  <YAxis
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    width={44}
                    domain={domain ?? ["auto", "auto"]}
                    allowDataOverflow={domain != null}
                    reversed={panel.reversed}
                    tickFormatter={panel.tick}
                  />
                  <Tooltip content={tooltipFor(panel, runs, axis)} />
                  {runs.map((run) => (
                    <Line
                      key={run.slot}
                      dataKey={`s${run.slot}`}
                      stroke={compareColor(run.slot)}
                      strokeWidth={1.6}
                      dot={false}
                      isAnimationActive={false}
                      connectNulls={false}
                      name={run.label}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </ChartFullscreen>
          </div>
        );
      })}
    </div>
  );
}
