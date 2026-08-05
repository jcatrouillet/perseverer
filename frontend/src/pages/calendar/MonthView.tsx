import { useState } from "react";
import { Link } from "wouter";

import { useCalendar, useCalendarWeeks } from "../../api/queries";
import { NotesPanel } from "../../components/NotesPanel";
import { eachDate, isoDate, mondayOf, monthName, monthRange, parseIsoDate } from "../../dateUtils";

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function MonthView({ year, month }: { year: number; month: number }) {
  const { start, end } = monthRange(year, month);
  const calendar = useCalendar(start, end);
  const [expandedDate, setExpandedDate] = useState<string | null>(null);

  const gridStart = mondayOf(parseIsoDate(start));
  const gridEnd = mondayOf(parseIsoDate(end));
  gridEnd.setUTCDate(gridEnd.getUTCDate() + 6);
  // Query the padded grid range, not just the month's own start/end -- otherwise the first/last
  // row's week total is missing whenever that week's Monday falls in the adjacent month.
  const weeks = useCalendarWeeks(isoDate(gridStart), isoDate(gridEnd));

  const dayByDate = new Map(calendar.data?.days.map((d) => [d.local_date, d]));
  const weekByStart = new Map(weeks.data?.periods.map((p) => [p.period_start, p]));
  const gridDates = eachDate(isoDate(gridStart), isoDate(gridEnd));
  const weekRows: string[][] = [];
  for (let i = 0; i < gridDates.length; i += 7) weekRows.push(gridDates.slice(i, i + 7));

  return (
    <main>
      <nav>
        <Link to={`/calendar/${year}`}>← {year}</Link>
      </nav>
      <h1>
        {monthName(month)} {year}
      </h1>
      {calendar.isLoading && <p>Loading…</p>}
      {calendar.isError && <p role="alert">Could not load the month.</p>}
      <table>
        <thead>
          <tr>
            {WEEKDAY_LABELS.map((label) => (
              <th key={label}>{label}</th>
            ))}
            <th>Week</th>
          </tr>
        </thead>
        <tbody>
          {weekRows.map((week) => {
            const weekRollup = weekByStart.get(week[0]!);
            return (
              <tr key={week[0]}>
                {week.map((date) => {
                  const inMonth = date >= start && date <= end;
                  const day = dayByDate.get(date);
                  if (!inMonth) return <td key={date} />;
                  return (
                    <td key={date}>
                      <button type="button" onClick={() => setExpandedDate(date)}>
                        {parseIsoDate(date).getUTCDate()}
                      </button>
                      {day && day.activity_count > 0 && (
                        <div>
                          {day.activity_count} act
                          {day.activity_distance_m != null &&
                            ` · ${(day.activity_distance_m / 1000).toFixed(1)}km`}
                          {day.activity_moving_duration_s != null &&
                            ` · ${(day.activity_moving_duration_s / 3600).toFixed(1)}h`}
                        </div>
                      )}
                    </td>
                  );
                })}
                <td>
                  {weekRollup && weekRollup.activity_count > 0 && (
                    <Link to={`/calendar/week/${week[0]}`}>
                      {weekRollup.activity_count} act
                      {weekRollup.activity_distance_m != null &&
                        ` · ${(weekRollup.activity_distance_m / 1000).toFixed(1)} km`}
                    </Link>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {expandedDate && (
        <section>
          <h2>{expandedDate}</h2>
          <NotesPanel entityType="day" entityId={expandedDate} />
        </section>
      )}
    </main>
  );
}
