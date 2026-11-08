// Health page, "DEXA scan": one scan at a time (the newest first, a picker when there are
// several) -- whole-body summary, regional composition, supplemental results, bone density and
// the left/right muscle balance -- plus the body-fat history across scans. Read-only here; scans
// are entered through POST /dexa-scans. Masses are kg (SI storage).
import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useDeleteDexaScan } from "../api/queries";
import type { DexaScanOut } from "../api/types";
import { formatDate } from "../bloodMarkers";
import {
  BODY_REGIONS,
  BONE_REGIONS,
  LIMB_SIDES,
  dexaValue,
  formatKg,
  formatNumber,
  formatPct,
  leanGapKg,
  leanPct,
} from "../dexaScans";
import "../styles/dexa.css";

function Summary({ scan }: { scan: DexaScanOut }) {
  const tiles: [string, string][] = [
    ["Body fat", formatPct(dexaValue(scan, "total", "fat_pct"))],
    ["Total mass", formatKg(dexaValue(scan, "total", "total_mass_kg"))],
    ["Fat tissue", formatKg(dexaValue(scan, "total", "fat_mass_kg"))],
    ["Lean tissue", formatKg(dexaValue(scan, "total", "lean_mass_kg"))],
    ["Bone mineral", formatKg(dexaValue(scan, "total", "bmc_kg"), 2)],
  ];
  return (
    <dl className="dexa__tiles">
      {tiles.map(([label, value]) => (
        <div key={label} className="dexa__tile">
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Regional({ scan }: { scan: DexaScanOut }) {
  const present = BODY_REGIONS.filter((r) => dexaValue(scan, r.key, "fat_pct") !== null);
  if (present.length === 0) return null;
  return (
    <section>
      <h3>Regional assessment</h3>
      <div className="dexa__scroll">
        <table className="dexa__table">
          <thead>
            <tr>
              <th>Region</th>
              <th>Fat %</th>
              <th>Total</th>
              <th>Fat</th>
              <th>Lean</th>
              <th>Lean %</th>
              <th>Bone</th>
            </tr>
          </thead>
          <tbody>
            {present.map((r) => (
              <tr key={r.key}>
                <th scope="row">{r.label}</th>
                <td>{formatPct(dexaValue(scan, r.key, "fat_pct"))}</td>
                <td>{formatKg(dexaValue(scan, r.key, "total_mass_kg"))}</td>
                <td>{formatKg(dexaValue(scan, r.key, "fat_mass_kg"))}</td>
                <td>{formatKg(dexaValue(scan, r.key, "lean_mass_kg"))}</td>
                <td>{formatPct(leanPct(scan, r.key))}</td>
                <td>{formatKg(dexaValue(scan, r.key, "bmc_kg"), 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Supplemental({ scan }: { scan: DexaScanOut }) {
  const rmr = dexaValue(scan, "total", "rmr_kcal_day");
  const ratio = dexaValue(scan, "total", "ag_ratio");
  const vatMass = dexaValue(scan, "total", "vat_mass_kg");
  const vatVolume = dexaValue(scan, "total", "vat_volume_cm3");
  const rows: [string, string][] = [];
  if (rmr !== null) rows.push(["Resting metabolic rate", `${Math.round(rmr)} kcal/day`]);
  if (ratio !== null) rows.push(["Android / gynoid ratio (target below 1.0)", ratio.toFixed(2)]);
  if (vatMass !== null) rows.push(["Visceral fat mass", `${vatMass.toFixed(2)} kg`]);
  if (vatVolume !== null) rows.push(["Visceral fat volume", `${Math.round(vatVolume)} cm³`]);
  if (rows.length === 0) return null;
  return (
    <section>
      <h3>Supplemental results</h3>
      <dl className="dexa__rows">
        {rows.map(([label, value]) => (
          <div key={label} className="dexa__row">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Bone({ scan }: { scan: DexaScanOut }) {
  const present = BONE_REGIONS.filter((r) => dexaValue(scan, r.key, "bmd_g_cm2") !== null);
  if (present.length === 0) return null;
  return (
    <section>
      <h3>Bone density</h3>
      <div className="dexa__scroll">
        <table className="dexa__table">
          <thead>
            <tr>
              <th>Region</th>
              <th>BMD (g/cm²)</th>
            </tr>
          </thead>
          <tbody>
            {present.map((r) => (
              <tr key={r.key}>
                <th scope="row">{r.label}</th>
                <td>{formatNumber(dexaValue(scan, r.key, "bmd_g_cm2"), 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="chart-note">
        Total: T-score {formatNumber(dexaValue(scan, "total", "t_score"), 1)}, Z-score{" "}
        {formatNumber(dexaValue(scan, "total", "z_score"), 1)}. A body-composition DEXA is not a
        bone density exam.
      </p>
    </section>
  );
}

function Balance({ scan }: { scan: DexaScanOut }) {
  const present = LIMB_SIDES.filter((r) => dexaValue(scan, r.key, "lean_mass_kg") !== null);
  if (present.length === 0) return null;
  const gaps: [string, number | null][] = [
    ["Arms", leanGapKg(scan, "arm")],
    ["Legs", leanGapKg(scan, "leg")],
  ];
  return (
    <section>
      <h3>Muscle balance</h3>
      <div className="dexa__scroll">
        <table className="dexa__table">
          <thead>
            <tr>
              <th>Limb</th>
              <th>Fat %</th>
              <th>Fat</th>
              <th>Lean</th>
            </tr>
          </thead>
          <tbody>
            {present.map((r) => (
              <tr key={r.key}>
                <th scope="row">{r.label}</th>
                <td>{formatPct(dexaValue(scan, r.key, "fat_pct"))}</td>
                <td>{formatKg(dexaValue(scan, r.key, "fat_mass_kg"))}</td>
                <td>{formatKg(dexaValue(scan, r.key, "lean_mass_kg"))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="dexa__gaps">
        {gaps.map(([label, gap]) =>
          gap === null ? null : (
            <li key={label}>
              {label}: {Math.abs(gap).toFixed(1)} kg more lean on the {gap >= 0 ? "right" : "left"}
            </li>
          ),
        )}
      </ul>
    </section>
  );
}

function History({ scans }: { scans: DexaScanOut[] }) {
  const points = [...scans]
    .reverse()
    .map((s) => ({ date: s.local_date, fat: dexaValue(s, "total", "fat_pct") }))
    .filter((p): p is { date: string; fat: number } => p.fat !== null);
  if (points.length < 2) return null;
  return (
    <section>
      <h3>Body fat across scans</h3>
      <div className="dexa__chart">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--color-border)" vertical={false} />
            <XAxis dataKey="date" tickFormatter={formatDate} stroke="var(--color-text-muted)" />
            <YAxis
              domain={["auto", "auto"]}
              tickFormatter={(v: number) => `${v}%`}
              stroke="var(--color-text-muted)"
              width={48}
            />
            <Tooltip
              formatter={(v) => [`${Number(v).toFixed(1)}%`, "Body fat"]}
              labelFormatter={(d) => formatDate(String(d))}
            />
            <Line
              dataKey="fat"
              stroke="var(--color-accent)"
              strokeWidth={2}
              dot={{ r: 4 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

export function DexaScanPanel({ scans }: { scans: DexaScanOut[] }) {
  const [picked, setPicked] = useState<string | null>(null);
  const remove = useDeleteDexaScan();
  const scan = scans.find((s) => s.local_date === picked) ?? scans[0];
  if (!scan) return <p className="chart-note">No DEXA scan recorded yet.</p>;

  return (
    <div className="dexa">
      <header className="dexa__header">
        {scans.length > 1 ? (
          <label>
            Scan{" "}
            <select
              className="input"
              value={scan.local_date}
              onChange={(e) => setPicked(e.target.value)}
            >
              {scans.map((s) => (
                <option key={s.local_date} value={s.local_date}>
                  {formatDate(s.local_date)}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <strong>{formatDate(scan.local_date)}</strong>
        )}
        {scan.provider && <span className="dexa__provider">{scan.provider}</span>}
        <button
          type="button"
          className="button"
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(`Delete the DEXA scan of ${formatDate(scan.local_date)}?`)) {
              remove.mutate(scan.local_date);
              setPicked(null);
            }
          }}
        >
          Delete scan
        </button>
      </header>
      <Summary scan={scan} />
      <History scans={scans} />
      <Regional scan={scan} />
      <Supplemental scan={scan} />
      <Bone scan={scan} />
      <Balance scan={scan} />
    </div>
  );
}
