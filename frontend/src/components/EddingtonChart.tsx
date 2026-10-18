// Insights' "Eddington Number" tab -- see eddington.ts's own docstring for the definition and
// why this is computed client-side over the same full activity-history fetch InsightsPage.tsx's
// Pace trends tab already performs, rather than a new backend endpoint. For runs or for rides
// (the Insights sport switch), never both mixed: the number counts one sport's activities.
import { useMemo } from "react";

import { useAllActivities } from "../api/queries";
import type { InsightsSport } from "../api/types";
import { computeEddingtonBars, computeYearlyEddington } from "../eddington";
import { metersToDisplayDistance, useDistanceFormat } from "../formatDistance";
import { INSIGHTS_SPORTS } from "../insightsSport";
import { EddingtonBarChart } from "./EddingtonBarChart";
import { LoadingSpinner } from "./LoadingSpinner";
import "../styles/eddington.css";

export function EddingtonChart({
  sport = "running",
  excludeCommutes = false,
}: {
  sport?: InsightsSport;
  excludeCommutes?: boolean;
}) {
  const copy = INSIGHTS_SPORTS[sport];
  const all = useAllActivities({ sport: copy.apiSport });
  // Commutes left out when the Insights toggle says so, before any Eddington count.
  const runs = {
    ...all,
    data: excludeCommutes ? all.data?.filter((a) => !a.is_commute) : all.data,
  };
  const { unit, unitLabel } = useDistanceFormat();
  const years = useMemo(() => computeYearlyEddington(runs.data ?? [], unit), [runs.data, unit]);

  const currentYear = new Date().getFullYear();
  // The bar chart shows the current year, or when it has nothing yet (a ride-free year so far,
  // say) the latest year that does, so the chart never just disappears.
  const { chartYear, chartBars } = useMemo(() => {
    const byYear = new Map<number, number[]>();
    for (const a of runs.data ?? []) {
      if (a.local_date == null || a.distance_m == null || a.distance_m <= 0) continue;
      const year = Number(a.local_date.slice(0, 4));
      if (year > currentYear) continue;
      byYear.set(year, [...(byYear.get(year) ?? []), metersToDisplayDistance(a.distance_m, unit)]);
    }
    if (byYear.size === 0) return { chartYear: null, chartBars: [] };
    const year = byYear.has(currentYear) ? currentYear : Math.max(...byYear.keys());
    return { chartYear: year, chartBars: computeEddingtonBars(byYear.get(year)!) };
  }, [runs.data, currentYear, unit]);
  const thisYear = chartYear === currentYear;

  if (runs.isLoading) return <LoadingSpinner />;
  if (runs.isError) return <p role="alert">Could not load your {copy.many}.</p>;
  if (years.length === 0) return null;

  return (
    <section className="card">
      <h2>Eddington number</h2>
      <p className="chart-note">
        The largest number E such that you have at least E {copy.many} of E {unitLabel} or further
        that year -- a classic cycling-logging statistic (VeloViewer and others)
        {sport === "running" ? ", applied here to running" : ""}. Raising it from E to E+1 always
        needs a whole additional {copy.one} of at least E+1 {unitLabel}, not just a longer one of
        your existing {copy.many}.
      </p>
      {chartYear != null && chartBars.length > 0 && (
        <>
          <p className="chart-note">
            {chartYear}
            {thisYear ? "" : ` (no ${copy.many} yet in ${currentYear}, so your latest year)`}: bar
            height is how many {copy.many} {thisYear ? "this year" : "that year"} reached at least
            that many {unitLabel}. Green while the bar still clears its own threshold, red once it
            falls short -- the crossing point with the dotted diagonal is{" "}
            {thisYear ? "this year's" : "that year's"} Eddington number.
          </p>
          <EddingtonBarChart bars={chartBars} unitLabel={unitLabel} />
        </>
      )}
      <div className="eddington-table__scroll">
        <table className="eddington-table">
          <thead>
            <tr>
              <th>Year</th>
              <th>Eddington number</th>
              <th>Total {copy.many}</th>
              <th>Progress to next</th>
            </tr>
          </thead>
          <tbody>
            {years.map((y) => (
              <tr key={y.year}>
                <td>{y.year}</td>
                <td className="eddington-table__number">{y.eddingtonNumber}</td>
                <td>{y.totalRuns}</td>
                <td>
                  {y.runsTowardNext} / {y.eddingtonNumber + 1} {copy.many} of{" "}
                  {y.eddingtonNumber + 1}+ {unitLabel} logged
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
