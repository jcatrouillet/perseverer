// Insights' "Eddington Number" tab -- see eddington.ts's own docstring for the definition and
// why this is computed client-side over the same full running-history fetch InsightsPage.tsx's
// Pace trends tab already performs, rather than a new backend endpoint.
import { useMemo } from "react";

import { useAllActivities } from "../api/queries";
import { computeEddingtonBars, computeYearlyEddington } from "../eddington";
import { metersToDisplayDistance, useDistanceFormat } from "../formatDistance";
import { EddingtonBarChart } from "./EddingtonBarChart";
import { LoadingSpinner } from "./LoadingSpinner";
import "../styles/eddington.css";

export function EddingtonChart() {
  const runs = useAllActivities({ sport: "running" });
  const { unit, unitLabel } = useDistanceFormat();
  const years = useMemo(() => computeYearlyEddington(runs.data ?? [], unit), [runs.data, unit]);

  const currentYear = new Date().getFullYear();
  const currentYearBars = useMemo(() => {
    const distances = (runs.data ?? [])
      .filter(
        (a) =>
          a.local_date != null &&
          Number(a.local_date.slice(0, 4)) === currentYear &&
          a.distance_m != null &&
          a.distance_m > 0,
      )
      .map((a) => metersToDisplayDistance(a.distance_m!, unit));
    return computeEddingtonBars(distances);
  }, [runs.data, currentYear, unit]);

  if (runs.isLoading) return <LoadingSpinner />;
  if (runs.isError) return <p role="alert">Could not load running activities.</p>;
  if (years.length === 0) return null;

  return (
    <section className="card">
      <h2>Eddington number</h2>
      <p className="chart-note">
        The largest number E such that you've run at least E times of E {unitLabel} or further,
        that year -- a classic cycling-logging statistic (VeloViewer and others), applied here to
        running. Raising it from E to E+1 always needs a whole additional run of at least E+1{" "}
        {unitLabel}, not just a longer one of your existing runs.
      </p>
      {currentYearBars.length > 0 && (
        <>
          <p className="chart-note">
            {currentYear}: bar height is how many runs this year reached at least that many{" "}
            {unitLabel}. Green while the bar still clears its own threshold, red once it falls
            short -- the crossing point with the dotted diagonal is this year's Eddington number.
          </p>
          <EddingtonBarChart bars={currentYearBars} unitLabel={unitLabel} />
        </>
      )}
      <div className="eddington-table__scroll">
        <table className="eddington-table">
          <thead>
            <tr>
              <th>Year</th>
              <th>Eddington number</th>
              <th>Total runs</th>
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
                  {y.runsTowardNext} / {y.eddingtonNumber + 1} runs of {y.eddingtonNumber + 1}+{" "}
                  {unitLabel} logged
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
