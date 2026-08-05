import { Link } from "wouter";

import { useCalendarMonths } from "../../api/queries";
import { monthName, yearRange } from "../../dateUtils";

export function YearView({ year }: { year: number }) {
  const { start, end } = yearRange(year);
  const months = useCalendarMonths(start, end);
  const byMonth = new Map(months.data?.periods.map((p) => [p.period_start.slice(5, 7), p]));

  return (
    <main>
      <nav>
        <Link to="/">Today</Link> · <Link to={`/calendar/${year - 1}`}>← {year - 1}</Link> ·{" "}
        <Link to={`/calendar/${year + 1}`}>{year + 1} →</Link>
      </nav>
      <h1>{year}</h1>
      {months.isLoading && <p>Loading…</p>}
      {months.isError && <p role="alert">Could not load the year.</p>}
      <ul>
        {Array.from({ length: 12 }, (_, i) => i + 1).map((month) => {
          const rollup = byMonth.get(String(month).padStart(2, "0"));
          return (
            <li key={month}>
              <Link to={`/calendar/${year}/${month}`}>{monthName(month)}</Link>
              {rollup && rollup.activity_count > 0 && (
                <>
                  {" — "}
                  {rollup.activity_count} activit{rollup.activity_count === 1 ? "y" : "ies"}
                  {rollup.activity_distance_m != null &&
                    ` · ${(rollup.activity_distance_m / 1000).toFixed(1)} km`}
                  {rollup.activity_moving_duration_s != null &&
                    ` · ${(rollup.activity_moving_duration_s / 3600).toFixed(1)}h`}
                </>
              )}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
