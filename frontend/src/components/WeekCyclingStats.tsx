// The "Cycling" card on the week view, WeekRunningStats' counterpart for rides: this week's
// totals, any all-time record distance set this week, and three charts over the last year --
// weekly distance, weekly best FTP (95% of the week's best 20 minutes) and power vs distance.
// Not rendered when the week has no ride.
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { useLocation } from "wouter";

import type { ActivitySummary } from "../api/types";
import { averageSpeedKmh, powerVsDistance, weeklyBestFtp } from "../cyclingStats";
import { useDistanceFormat } from "../formatDistance";
import { toneColor } from "../metricStyle";
import {
  CYCLING_PR_DISTANCES,
  effectiveDurationS,
  formatDurationHM,
  newAllTimePrs,
  personalRecords,
  weeklyDistanceSeries,
} from "../runningStats";
import { ChartFullscreen } from "./ChartFullscreen";
import { Icon } from "./Icon";
import { SportSectionTitle } from "./SportSectionTitle";
import { StatTile } from "./StatTile";
import "../styles/running-stats.css";

const RIDE = toneColor("ride");
const POWER = toneColor("power");
const FADED = "var(--color-text-faint)";
const TOOLTIP_STYLE = {
  background: "var(--color-surface-raised)",
  border: "1px solid var(--color-border)",
};

function formatWeekTick(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function WeekCyclingStats({
  allRides,
  rangeStart,
  weekStart,
  weekEnd,
}: {
  /** Every ride ever: this week's rides, the last year's for the charts, and all of them for
   * the all-time records. */
  allRides: ActivitySummary[];
  /** First day of the trailing year the charts cover; the last day is `weekEnd`. */
  rangeStart: string;
  weekStart: string;
  weekEnd: string;
}) {
  const [, setLocation] = useLocation();
  const { metersToDisplay, unitLabel, kmhToDisplay, speedUnitLabel } = useDistanceFormat();

  const inWeek = (a: ActivitySummary) =>
    a.local_date != null && a.local_date >= weekStart && a.local_date <= weekEnd;
  const thisWeek = allRides.filter(inWeek);
  if (thisWeek.length === 0) return null;

  const window = allRides.filter(
    (a) => a.local_date != null && a.local_date >= rangeStart && a.local_date <= weekEnd,
  );
  const newPrs = newAllTimePrs(
    personalRecords(thisWeek, CYCLING_PR_DISTANCES),
    personalRecords(allRides, CYCLING_PR_DISTANCES),
  );

  const totalDistanceM = thisWeek.reduce((sum, a) => sum + (a.distance_m ?? 0), 0);
  const totalDurationS = thisWeek.reduce((sum, a) => sum + (effectiveDurationS(a) ?? 0), 0);
  const totalCalories = thisWeek.reduce((sum, a) => sum + (a.calories ?? 0), 0);
  const totalElevationM = thisWeek.reduce((sum, a) => sum + (a.elevation_gain_m ?? 0), 0);
  // The fastest ride's average speed, as Running shows the fastest pace.
  const fastestKmh = thisWeek.reduce<number | null>((fastest, a) => {
    const speed = averageSpeedKmh([a]);
    return speed != null && (fastest == null || speed > fastest) ? speed : fastest;
  }, null);
  const dayEquivalentPct = (totalDurationS / 86400) * 100;

  const weeklySeries = weeklyDistanceSeries(window, rangeStart, weekEnd);
  const ftpSeries = weeklyBestFtp(window, rangeStart, weekEnd);
  const weekIds = new Set(thisWeek.map((a) => a.id));
  const scatterPoints = powerVsDistance(window)
    .map((p) => ({ ...p, isThisWeek: weekIds.has(p.id) }))
    // SVG has no z-index: this week's dots go last so they paint on top of the faded ones.
    .sort((a, b) => Number(a.isThisWeek) - Number(b.isThisWeek));

  const weekFill = (week: string) => (week === weekStart ? RIDE : FADED);
  const weekTooltip = (v: unknown) => `Week of ${formatWeekTick(String(v))}`;

  return (
    <section className="card running-stats">
      <SportSectionTitle sport="cycling">Cycling</SportSectionTitle>
      {newPrs.length > 0 && (
        <p className="running-records__new-prs">
          <Icon name="trophy" /> {newPrs.length} all-time record{newPrs.length === 1 ? "" : "s"} set
          this week: {newPrs.map((r) => r.label).join(", ")}
        </p>
      )}
      <div className="stat-grid">
        <StatTile
          label="Total distance"
          value={metersToDisplay(totalDistanceM).toFixed(1)}
          unit={unitLabel}
          icon="route"
          tone="ride"
          hero
        />
        <StatTile
          label="Total duration"
          value={formatDurationHM(totalDurationS)}
          icon="clock"
          tone="cadence"
          hero
        />
        {fastestKmh != null && (
          <StatTile
            label="Fastest speed"
            value={kmhToDisplay(fastestKmh).toFixed(1)}
            unit={speedUnitLabel}
            icon="gauge"
            tone="ride"
          />
        )}
        <StatTile
          label="Day equivalent"
          value={dayEquivalentPct.toFixed(0)}
          unit="%"
          meta="of a full day spent riding"
          icon="calendar"
          tone="elevation"
        />
        {totalCalories > 0 && (
          <StatTile
            label="Calories burned"
            value={Math.round(totalCalories)}
            unit="kcal"
            icon="flame"
            tone="load"
          />
        )}
        {totalElevationM > 0 && (
          <StatTile
            label="Total elevation"
            value={totalElevationM.toFixed(0)}
            unit="m"
            icon="mountain"
            tone="elevation"
          />
        )}
      </div>

      <div className="running-stats__charts">
        <div>
          <ChartFullscreen as="h3" title="Weekly distance">
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={weeklySeries}>
                <CartesianGrid
                  stroke="var(--color-border)"
                  strokeDasharray="3 3"
                  vertical={false}
                />
                <XAxis
                  dataKey="weekStart"
                  stroke="var(--color-text-muted)"
                  fontSize={11}
                  tickFormatter={formatWeekTick}
                  interval={Math.max(0, Math.ceil(weeklySeries.length / 8) - 1)}
                />
                <YAxis stroke="var(--color-text-muted)" fontSize={11} width={32} />
                <Tooltip
                  labelFormatter={weekTooltip}
                  formatter={(value) => [`${value} km`, "Distance"]}
                  contentStyle={TOOLTIP_STYLE}
                />
                <Bar dataKey="km" isAnimationActive={false}>
                  {weeklySeries.map((p) => (
                    <Cell key={p.weekStart} fill={weekFill(p.weekStart)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartFullscreen>
        </div>

        <div>
          <ChartFullscreen as="h3" title="Weekly best FTP">
            <ResponsiveContainer width="100%" height={140}>
              <BarChart data={ftpSeries}>
                <CartesianGrid
                  stroke="var(--color-border)"
                  strokeDasharray="3 3"
                  vertical={false}
                />
                <XAxis
                  dataKey="weekStart"
                  stroke="var(--color-text-muted)"
                  fontSize={11}
                  tickFormatter={formatWeekTick}
                  interval={Math.max(0, Math.ceil(ftpSeries.length / 8) - 1)}
                />
                <YAxis
                  stroke="var(--color-text-muted)"
                  fontSize={11}
                  width={40}
                  unit=" W"
                  domain={["dataMin - 10", "dataMax + 10"]}
                />
                <Tooltip
                  labelFormatter={weekTooltip}
                  formatter={(value) => [value == null ? "No power" : `${value} W`, "Best FTP"]}
                  contentStyle={TOOLTIP_STYLE}
                />
                <Bar
                  dataKey="ftp"
                  isAnimationActive={false}
                  cursor="pointer"
                  onClick={(point: { payload?: { activityId: string | null } }) => {
                    if (point.payload?.activityId)
                      setLocation(`/activities/${point.payload.activityId}`);
                  }}
                >
                  {ftpSeries.map((p) => (
                    <Cell key={p.weekStart} fill={weekFill(p.weekStart)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartFullscreen>
        </div>

        <div>
          <ChartFullscreen as="h3" title="Power vs distance">
            {scatterPoints.length === 0 ? (
              <p className="chart-note">
                No ride in the last year recorded power (it needs a power meter or a smart trainer).
              </p>
            ) : (
              <ResponsiveContainer width="100%" height={140}>
                <ScatterChart>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                  <XAxis
                    dataKey="km"
                    type="number"
                    name="Distance"
                    unit=" km"
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                  />
                  <YAxis
                    dataKey="watts"
                    type="number"
                    name="Average power"
                    unit=" W"
                    stroke="var(--color-text-muted)"
                    fontSize={11}
                    width={52}
                    domain={["dataMin - 10", "dataMax + 10"]}
                  />
                  <ZAxis range={[36, 90]} />
                  <Tooltip cursor={{ strokeDasharray: "3 3" }} contentStyle={TOOLTIP_STYLE} />
                  <Scatter
                    data={scatterPoints}
                    isAnimationActive={false}
                    cursor="pointer"
                    onClick={(point: { payload?: { id: string } }) => {
                      if (point.payload) setLocation(`/activities/${point.payload.id}`);
                    }}
                  >
                    {scatterPoints.map((p) => (
                      <Cell
                        key={p.id}
                        fill={p.isThisWeek ? POWER : FADED}
                        fillOpacity={p.isThisWeek ? 1 : 0.35}
                      />
                    ))}
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
            )}
          </ChartFullscreen>
        </div>
      </div>
    </section>
  );
}
