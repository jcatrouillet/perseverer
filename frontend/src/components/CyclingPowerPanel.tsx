// Insights' cycling "FTP & VO2max" tab: FTP, FTP/kg, Critical Power (with W') and a VO2max
// estimate from the rides' own power data -- the cycling counterparts of VDOT and the running
// VO2max, computed by Perseverer (ride_power.py), never Garmin's figures. Current values over the
// trailing window, how they moved, and the rides behind the bests.
import { Link } from "wouter";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useCyclingPower } from "../api/queries";
import type { CyclingPowerPointOut } from "../api/types";
import { toneColor } from "../metricStyle";
import { ChartFullscreen } from "./ChartFullscreen";
import { ChartLegend } from "./ChartLegend";
import { LoadingSpinner } from "./LoadingSpinner";
import { StatTile } from "./StatTile";

const POWER = toneColor("power");
const RIDE = toneColor("ride");
const VO2 = toneColor("cadence");
const TOOLTIP_STYLE = {
  background: "var(--color-surface-raised)",
  border: "1px solid var(--color-border)",
};

function round(value: number | null, digits = 0): number | null {
  return value == null ? null : Number(value.toFixed(digits));
}

function formatMinutes(seconds: number): string {
  return seconds < 60 ? `${seconds} s` : `${seconds / 60} min`;
}

// The bests behind FTP (20 min), VO2max (5 min) and Critical Power (3-20 min).
const FIGURE_DURATIONS_S = new Set([180, 300, 720, 1200]);

export function CyclingPowerPanel({ excludeCommutes = false }: { excludeCommutes?: boolean }) {
  const power = useCyclingPower(excludeCommutes);
  if (power.isLoading) return <LoadingSpinner />;
  if (power.isError) return <p role="alert">Could not load FTP and VO2max.</p>;
  if (!power.data) return null;

  const { current, history, window_days: windowDays, missing, last_power_ride_date } = power.data;
  // Shown as of the last power ride when the window ending today holds none.
  const shown: CyclingPowerPointOut | null = current ?? history.at(-1) ?? null;
  const asOfNote = current
    ? `the last ${windowDays} days`
    : `${windowDays} days to ${last_power_ride_date}`;
  const chartData = history.map((p) => ({
    date: p.as_of,
    ftp: round(p.ftp_w),
    cp: round(p.cp_w),
    vo2max: round(p.vo2max, 1),
    ftpPerKg: round(p.ftp_w_per_kg, 2),
  }));

  return (
    <>
      <section className="card">
        <h2>FTP &amp; VO2max</h2>
        <p className="chart-note">
          Computed from your own rides with power (a power meter or a smart trainer), over{" "}
          {asOfNote}. FTP is 95% of your best 20 minutes; FTP/kg divides it by your weight. Critical
          Power and W′ come from fitting your best 3, 5, 12 and 20 minutes to the two-parameter
          model (work = W′ + CP × time). VO2max uses the ACSM cycling equation (10.8 × W/kg + 7) on
          your best 5 minutes, so it reads true only when those 5 minutes were close to all-out.
        </p>
        {missing.length > 0 && (
          <ul className="vo2max-factors__missing">
            {missing.map((m) => (
              <li key={m} className="vo2max-factors__missing-item">
                {m}
              </li>
            ))}
          </ul>
        )}
        {shown && (
          <div className="stat-grid">
            <StatTile
              label="FTP"
              value={shown.ftp_w != null ? Math.round(shown.ftp_w) : "—"}
              unit="W"
              icon="bolt"
              tone="power"
              hero
            />
            <StatTile
              label="FTP/kg"
              value={shown.ftp_w_per_kg != null ? shown.ftp_w_per_kg.toFixed(2) : "—"}
              unit="W/kg"
              meta={shown.weight_kg != null ? `at ${shown.weight_kg.toFixed(1)} kg` : null}
              icon="bolt"
              tone="power"
              hero
            />
            <StatTile
              label="Critical Power"
              value={shown.cp_w != null ? Math.round(shown.cp_w) : "—"}
              unit="W"
              meta={shown.w_prime_j != null ? `W′ ${(shown.w_prime_j / 1000).toFixed(1)} kJ` : null}
              icon="gauge"
              tone="ride"
              hero
            />
            <StatTile
              label="VO2max (estimate)"
              value={shown.vo2max != null ? shown.vo2max.toFixed(1) : "—"}
              unit="ml/kg/min"
              icon="gauge"
              tone="cadence"
            />
          </div>
        )}
      </section>

      {chartData.length > 1 && (
        <section className="card">
          <ChartFullscreen as="h3" title="FTP and Critical Power over time">
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                <XAxis dataKey="date" stroke="var(--color-text-muted)" fontSize={11} />
                <YAxis unit=" W" stroke="var(--color-text-muted)" fontSize={11} width={56} />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Line
                  dataKey="ftp"
                  name="FTP"
                  stroke={POWER}
                  strokeWidth={2}
                  connectNulls
                  isAnimationActive={false}
                />
                <Line
                  dataKey="cp"
                  name="Critical Power"
                  stroke={RIDE}
                  strokeWidth={2}
                  strokeDasharray="5 4"
                  connectNulls
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
            <ChartLegend
              center
              items={[
                { label: "FTP", color: POWER },
                { label: "Critical Power", color: RIDE },
              ]}
            />
          </ChartFullscreen>
          <ChartFullscreen as="h3" title="FTP/kg over time">
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                <XAxis dataKey="date" stroke="var(--color-text-muted)" fontSize={11} />
                <YAxis
                  unit=" W/kg"
                  domain={["auto", "auto"]}
                  stroke="var(--color-text-muted)"
                  fontSize={11}
                  width={64}
                />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Line
                  dataKey="ftpPerKg"
                  name="FTP/kg"
                  stroke={POWER}
                  strokeWidth={2}
                  connectNulls
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </ChartFullscreen>
          <ChartFullscreen as="h3" title="VO2max over time">
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                <XAxis dataKey="date" stroke="var(--color-text-muted)" fontSize={11} />
                <YAxis
                  unit=" ml/kg/min"
                  domain={["auto", "auto"]}
                  stroke="var(--color-text-muted)"
                  fontSize={11}
                  width={80}
                />
                <Tooltip contentStyle={TOOLTIP_STYLE} />
                <Line
                  dataKey="vo2max"
                  name="VO2max"
                  stroke={VO2}
                  strokeWidth={2}
                  connectNulls
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </ChartFullscreen>
        </section>
      )}

      {shown && shown.best_powers.length > 0 && (
        <section className="card">
          <h3>Best efforts behind these figures</h3>
          <div className="table-scroll">
            <table className="running-records__table">
              <thead>
                <tr>
                  <th>Duration</th>
                  <th>Best power</th>
                  <th>Ride</th>
                </tr>
              </thead>
              <tbody>
                {shown.best_powers
                  .filter((b) => FIGURE_DURATIONS_S.has(b.duration_s))
                  .map((b) => (
                    <tr key={b.duration_s}>
                      <td>{formatMinutes(b.duration_s)}</td>
                      <td>{Math.round(b.watts)} W</td>
                      <td>
                        <Link href={`/activities/${b.activity_id}`}>
                          {b.name ?? "Ride"} · {b.local_date}
                        </Link>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
