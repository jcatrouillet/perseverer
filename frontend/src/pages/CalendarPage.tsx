// Default landing view -- rollup-backed, the one endpoint guaranteed not to scan (CLAUDE.md's
// rollup mandate). Date-range picker defaulting to the current week.
import { useState } from "react";
import { Link } from "wouter";

import { useCalendar } from "../api/queries";
import { NotesPanel } from "../components/NotesPanel";

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function startOfWeek(date: Date): Date {
  const d = new Date(date);
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day; // Monday as the first day of the week
  d.setDate(d.getDate() + diff);
  return d;
}

function defaultRange(): { start: string; end: string } {
  const today = new Date();
  const start = startOfWeek(today);
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  return { start: isoDate(start), end: isoDate(end) };
}

export function CalendarPage() {
  const [range, setRange] = useState(defaultRange);
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  const calendar = useCalendar(range.start, range.end);

  return (
    <main>
      <h1>Calendar</h1>
      <form>
        <label>
          From
          <input
            type="date"
            value={range.start}
            onChange={(e) => setRange((r) => ({ ...r, start: e.target.value }))}
          />
        </label>
        <label>
          To
          <input
            type="date"
            value={range.end}
            onChange={(e) => setRange((r) => ({ ...r, end: e.target.value }))}
          />
        </label>
      </form>

      {calendar.isLoading && <p>Loading…</p>}
      {calendar.isError && <p role="alert">Could not load the calendar.</p>}
      {calendar.data && calendar.data.days.length === 0 && (
        <p>No activity or health data in this range.</p>
      )}

      <ul>
        {calendar.data?.days.map((day) => (
          <li key={day.local_date}>
            <button type="button" onClick={() => setExpandedDate(day.local_date)}>
              {day.local_date}
            </button>{" "}
            {day.activity_count} activit{day.activity_count === 1 ? "y" : "ies"}
            {day.activity_distance_m != null &&
              ` · ${(day.activity_distance_m / 1000).toFixed(1)} km`}
            {day.sleep_total_s != null && ` · ${(day.sleep_total_s / 3600).toFixed(1)}h sleep`}
            {day.health_metrics.length > 0 && (
              <ul>
                {day.health_metrics.map((m) => (
                  <li key={m.metric_key}>
                    {m.metric_key}: {m.value_last ?? m.value_avg ?? m.value_sum}
                  </li>
                ))}
              </ul>
            )}
            {expandedDate === day.local_date && (
              <NotesPanel entityType="day" entityId={day.local_date} />
            )}
          </li>
        ))}
      </ul>

      <Link to="/activities">All activities →</Link>
    </main>
  );
}
