// One blood-test marker over time (Health page, one left-hand list entry per marker): the values
// as a line, the athlete's own reference range from each report as dashed step lines, and every
// value outside the range printed on its own report drawn as a red dot -- plus the same results
// as an editable table below the chart. Ranges are never asserted by this app; each point is
// judged only against the range stored on that same result (see bloodMarkers.ts).
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { BloodTestResultOut } from "../api/types";
import { markerDescription } from "../bloodMarkerInfo";
import { formatDate, formatRange, isOutOfRange } from "../bloodMarkers";
import { parseIsoDate } from "../dateUtils";
import { ChartLegend } from "./ChartLegend";
import { ResultRow } from "./BloodTestsPanel";

interface Row {
  t: number;
  result: BloodTestResultOut;
  value: number;
  low: number | null;
  high: number | null;
  out: boolean;
}

const DAY_MS = 86_400_000;

function tickLabel(t: number): string {
  return String(new Date(t).getUTCFullYear());
}

/** Jan 1 of every Nth year inside [from, to], N chosen so no more than ~8 ticks are drawn --
 * Recharts' own automatic ticks on a time-scaled numeric axis land on arbitrary dates that mean
 * nothing against a sparse, decades-long series of lab draws. */
function yearTicks(from: number, to: number): number[] {
  const firstYear = new Date(from).getUTCFullYear() + 1;
  const lastYear = new Date(to).getUTCFullYear();
  const step = Math.max(1, Math.ceil((lastYear - firstYear + 1) / 8));
  const ticks: number[] = [];
  for (let y = firstYear; y <= lastYear; y += step) ticks.push(Date.UTC(y, 0, 1));
  return ticks;
}

function buildRows(results: BloodTestResultOut[]): Row[] {
  return results.map((r) => ({
    t: parseIsoDate(r.local_date).getTime(),
    result: r,
    value: r.value_num,
    low: r.reference_low,
    high: r.reference_high,
    out: isOutOfRange(r),
  }));
}

function ValueDot(props: { cx?: number; cy?: number; payload?: Row; index?: number }) {
  const { cx, cy, payload, index } = props;
  if (cx == null || cy == null || !payload) return <g key={`empty-${index}`} />;
  return payload.out ? (
    <circle
      key={`dot-${index}`}
      cx={cx}
      cy={cy}
      r={6}
      fill="var(--color-danger)"
      stroke="var(--color-surface)"
      strokeWidth={2}
      data-testid="blood-marker-flagged-dot"
    />
  ) : (
    <circle key={`dot-${index}`} cx={cx} cy={cy} r={3.5} fill="var(--color-accent)" />
  );
}

function MarkerTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: Row }[];
}) {
  const row = active ? payload?.[0]?.payload : undefined;
  if (!row) return null;
  const r = row.result;
  return (
    <div className="blood-marker-tooltip">
      <strong>{formatDate(r.local_date)}</strong>
      <div>
        {r.value_num}
        {r.unit ? ` ${r.unit}` : ""}
        {row.out && <span className="blood-marker-tooltip__flag"> · outside range</span>}
      </div>
      <div className="chart-note">Reference: {formatRange(r)}</div>
      {r.lab_name && <div className="chart-note">{r.lab_name}</div>}
    </div>
  );
}

export function BloodMarkerChart({
  marker,
  results,
}: {
  marker: string;
  /** This marker's results, oldest first. */
  results: BloodTestResultOut[];
}) {
  const rows = buildRows(results);
  const flagged = rows.filter((r) => r.out).length;
  const unit = results.find((r) => r.unit)?.unit ?? null;
  const hasRange = rows.some((r) => r.low != null || r.high != null);

  const first = rows[0]!.t;
  const last = rows[rows.length - 1]!.t;
  // A single result (or several on one day) would collapse the axis to a point -- give it room.
  const pad = last === first ? 180 * DAY_MS : Math.max((last - first) * 0.04, 15 * DAY_MS);

  const description = markerDescription(marker);
  const latest = results[results.length - 1]!;
  const newestFirst = [...results].reverse();

  return (
    <div className="blood-marker">
      <h2 className="blood-marker__title">
        {marker}
        {unit && <span className="blood-marker__unit">{unit}</span>}
      </h2>
      {description && <p className="blood-marker__description">{description}</p>}
      <p className="chart-note">
        Latest {formatDate(latest.local_date)}: {latest.value_num}
        {latest.unit ? ` ${latest.unit}` : ""}
        {isOutOfRange(latest) ? " — outside its reference range" : ""}. {results.length} result
        {results.length === 1 ? "" : "s"}
        {flagged > 0 ? `, ${flagged} outside the range on their own report.` : "."}
      </p>

      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={rows} margin={{ top: 12, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={[first - pad, last + pad]}
            ticks={yearTicks(first - pad, last + pad)}
            tickFormatter={tickLabel}
            stroke="var(--color-text-muted)"
            fontSize={11}
          />
          <YAxis stroke="var(--color-text-muted)" fontSize={11} width={48} domain={["auto", "auto"]} />
          <Tooltip content={<MarkerTooltip />} />
          {hasRange && (
            <Line
              isAnimationActive={false}
              dataKey="high"
              name="Reference high"
              type="stepAfter"
              stroke="var(--color-warning)"
              strokeDasharray="5 4"
              strokeWidth={1.5}
              dot={false}
              activeDot={false}
              connectNulls
            />
          )}
          {hasRange && (
            <Line
              isAnimationActive={false}
              dataKey="low"
              name="Reference low"
              type="stepAfter"
              stroke="var(--color-warning)"
              strokeDasharray="5 4"
              strokeWidth={1.5}
              dot={false}
              activeDot={false}
              connectNulls
            />
          )}
          <Line
            isAnimationActive={false}
            dataKey="value"
            name={marker}
            type="monotone"
            stroke="var(--color-accent)"
            strokeWidth={2}
            dot={<ValueDot />}
            activeDot={{ r: 6 }}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <ChartLegend
        items={[
          { label: marker, color: "var(--color-accent)" },
          ...(hasRange ? [{ label: "Reference range (from each report)", color: "var(--color-warning)" }] : []),
          { label: "Outside reference range", color: "var(--color-danger)" },
        ]}
      />

      <div className="blood-tests__scroll">
        <table className="blood-tests__table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Value</th>
              <th>Unit</th>
              <th>Reference range</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {newestFirst.map((r) => (
              <ResultRow key={r.id} result={r} lead="date" />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
