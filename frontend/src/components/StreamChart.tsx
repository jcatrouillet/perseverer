// Minimal hand-rolled SVG line chart -- no charting library (see ADR 0008: the stream is
// already server-downsampled to the "low" tier, so a bounded point count renders fine as
// plain SVG, and this app has exactly one chart need). Renders one channel at a time with a
// selector, since channel names are source-driven (FIT record fields), not a fixed enum.
import { useMemo, useState } from "react";

import type { StreamResponse } from "../api/types";

const WIDTH = 720;
const HEIGHT = 220;
const PADDING = 32;

function formatClockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function StreamChart({ stream }: { stream: StreamResponse }) {
  const preferredDefault = stream.channels.includes("heart_rate")
    ? "heart_rate"
    : stream.channels[0];
  const [channel, setChannel] = useState(preferredDefault);

  const path = useMemo(() => {
    const values = stream.series[channel] ?? [];
    const timestamps = stream.timestamps.map((t) => new Date(t).getTime());
    const points: { x: number; y: number }[] = [];
    const numericValues = values.filter((v): v is number => v !== null);
    if (numericValues.length === 0 || timestamps.length < 2) return { d: "", min: 0, max: 0 };

    const minValue = Math.min(...numericValues);
    const maxValue = Math.max(...numericValues);
    const valueRange = maxValue - minValue || 1;
    const startMs = timestamps[0]!;
    const endMs = timestamps[timestamps.length - 1]!;
    const timeRange = endMs - startMs || 1;

    for (let i = 0; i < values.length; i++) {
      const value = values[i];
      if (value === null) continue;
      const x = PADDING + ((timestamps[i]! - startMs) / timeRange) * (WIDTH - 2 * PADDING);
      const y = HEIGHT - PADDING - ((value - minValue) / valueRange) * (HEIGHT - 2 * PADDING);
      points.push({ x, y });
    }

    const d = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
    return { d, min: minValue, max: maxValue };
  }, [stream, channel]);

  if (stream.channels.length === 0) {
    return <p>No stream data available.</p>;
  }

  return (
    <div>
      <label>
        Channel{" "}
        <select value={channel} onChange={(e) => setChannel(e.target.value)}>
          {stream.channels.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        width="100%"
        role="img"
        aria-label={`${channel} over time`}
      >
        <path d={path.d} fill="none" stroke="currentColor" strokeWidth={2} />
      </svg>
      <div>
        <span>{stream.timestamps[0] ? formatClockTime(stream.timestamps[0]) : ""}</span>
        {" · min "}
        <span>{path.min.toFixed(1)}</span>
        {" · max "}
        <span>{path.max.toFixed(1)}</span>
        {" · "}
        <span>
          {stream.timestamps.length > 0
            ? formatClockTime(stream.timestamps[stream.timestamps.length - 1]!)
            : ""}
        </span>
      </div>
    </div>
  );
}
