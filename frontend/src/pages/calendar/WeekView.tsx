import { useState } from "react";
import { Link } from "wouter";

import { useCalendar, useCalendarWeeks } from "../../api/queries";
import { NotesPanel } from "../../components/NotesPanel";
import { eachDate, weekRange } from "../../dateUtils";

export function WeekView({ date }: { date: string }) {
  const { start, end } = weekRange(date);
  const calendar = useCalendar(start, end);
  const weeks = useCalendarWeeks(start, end);
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  const weekTotal = weeks.data?.periods[0];
  const monthOfWeekStart = start.slice(0, 7); // YYYY-MM

  return (
    <main>
      <nav>
        <Link
          to={`/calendar/${monthOfWeekStart.slice(0, 4)}/${Number(monthOfWeekStart.slice(5, 7))}`}
        >
          ← Month
        </Link>
      </nav>
      <h1>
        Week of {start} – {end}
      </h1>
      {weekTotal && weekTotal.activity_count > 0 && (
        <p>
          {weekTotal.activity_count} activit{weekTotal.activity_count === 1 ? "y" : "ies"}
          {weekTotal.activity_distance_m != null &&
            ` · ${(weekTotal.activity_distance_m / 1000).toFixed(1)} km`}
          {weekTotal.activity_moving_duration_s != null &&
            ` · ${(weekTotal.activity_moving_duration_s / 3600).toFixed(1)}h`}
          {` · ${weekTotal.activity_days_count} of 7 days active`}
        </p>
      )}
      {calendar.isLoading && <p>Loading…</p>}
      {calendar.isError && <p role="alert">Could not load the week.</p>}
      <ul>
        {eachDate(start, end).map((d) => {
          const day = calendar.data?.days.find((x) => x.local_date === d);
          return (
            <li key={d}>
              <button type="button" onClick={() => setExpandedDate(d)}>
                {d}
              </button>
              {day && day.activity_count > 0 && (
                <>
                  {" — "}
                  {day.activity_count} activit{day.activity_count === 1 ? "y" : "ies"}
                  {day.activity_distance_m != null &&
                    ` · ${(day.activity_distance_m / 1000).toFixed(1)} km`}
                  {day.activity_moving_duration_s != null &&
                    ` · ${(day.activity_moving_duration_s / 3600).toFixed(1)}h`}
                  {day.sleep_total_s != null &&
                    ` · ${(day.sleep_total_s / 3600).toFixed(1)}h sleep`}
                </>
              )}
              {expandedDate === d && <NotesPanel entityType="day" entityId={d} />}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
