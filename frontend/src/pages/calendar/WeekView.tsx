import { useState } from "react";

import { useCalendar, useCalendarWeeks } from "../../api/queries";
import { DateNavigator } from "../../components/DateNavigator";
import { NotesPanel } from "../../components/NotesPanel";
import { eachDate, weekRange } from "../../dateUtils";
import "../../styles/calendar.css";

export function WeekView({ date }: { date: string }) {
  const { start, end } = weekRange(date);
  const calendar = useCalendar(start, end);
  const weeks = useCalendarWeeks(start, end);
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  const weekTotal = weeks.data?.periods[0];
  const monthOfWeekStart = start.slice(0, 7); // YYYY-MM

  return (
    <main>
      <DateNavigator
        year={Number(monthOfWeekStart.slice(0, 4))}
        month={Number(monthOfWeekStart.slice(5, 7))}
        selectedDate={start}
      />
      <h1>
        Week of {start} – {end}
      </h1>
      {weekTotal && weekTotal.activity_count > 0 && (
        <p className="week-summary">
          <span>
            <strong>{weekTotal.activity_count}</strong> activit
            {weekTotal.activity_count === 1 ? "y" : "ies"}
          </span>
          {weekTotal.activity_distance_m != null && (
            <span>
              <strong>{(weekTotal.activity_distance_m / 1000).toFixed(1)}</strong> km
            </span>
          )}
          {weekTotal.activity_moving_duration_s != null && (
            <span>
              <strong>{(weekTotal.activity_moving_duration_s / 3600).toFixed(1)}</strong>h
            </span>
          )}
          <span>
            <strong>{weekTotal.activity_days_count}</strong> of 7 days active
          </span>
        </p>
      )}
      {calendar.isLoading && <p>Loading…</p>}
      {calendar.isError && <p role="alert">Could not load the week.</p>}
      <ul className="week-day-list">
        {eachDate(start, end).map((d) => {
          const day = calendar.data?.days.find((x) => x.local_date === d);
          return (
            <li key={d} className="card">
              <div className="week-day-list__header">
                <button type="button" onClick={() => setExpandedDate(d)}>
                  {d}
                </button>
                {day && day.activity_count > 0 && (
                  <span className="week-day-list__meta">
                    {day.activity_count} activit{day.activity_count === 1 ? "y" : "ies"}
                    {day.activity_distance_m != null &&
                      ` · ${(day.activity_distance_m / 1000).toFixed(1)} km`}
                    {day.activity_moving_duration_s != null &&
                      ` · ${(day.activity_moving_duration_s / 3600).toFixed(1)}h`}
                    {day.sleep_total_s != null &&
                      ` · ${(day.sleep_total_s / 3600).toFixed(1)}h sleep`}
                  </span>
                )}
              </div>
              {expandedDate === d && <NotesPanel entityType="day" entityId={d} />}
            </li>
          );
        })}
      </ul>
    </main>
  );
}
