import { Link } from "wouter";

import { useActivities, useCalendar } from "../api/queries";
import { DateNavigator } from "../components/DateNavigator";
import { NotesPanel } from "../components/NotesPanel";
import { mondayOf, parseIsoDate } from "../dateUtils";
import "../styles/calendar.css";

// A first, functional cut of the day view -- combines that day's activities and rollup summary
// with notes. Milestone D (see docs/adr/0010-phase-6.1-frontend-design.md) will enrich this
// further with Fitness & Form and health tiles for the date; pulled forward now so the
// DateNavigator's day-click can go somewhere real rather than a stub.
export function DayViewPage({ date }: { date: string }) {
  const calendar = useCalendar(date, date);
  const activities = useActivities({ startDate: date, endDate: date, limit: 50 });
  const day = calendar.data?.days[0];
  const weekStart = mondayOf(parseIsoDate(date));
  const year = weekStart.getUTCFullYear();
  const month = weekStart.getUTCMonth() + 1;

  return (
    <main>
      <DateNavigator year={year} month={month} selectedDate={date} />
      <nav>
        <Link to={`/calendar/week/${weekStart.toISOString().slice(0, 10)}`}>← Week</Link>
      </nav>
      <h1>{date}</h1>

      {day && day.activity_count > 0 && (
        <p className="week-summary">
          <span>
            <strong>{day.activity_count}</strong> activit
            {day.activity_count === 1 ? "y" : "ies"}
          </span>
          {day.activity_distance_m != null && (
            <span>
              <strong>{(day.activity_distance_m / 1000).toFixed(1)}</strong> km
            </span>
          )}
          {day.activity_moving_duration_s != null && (
            <span>
              <strong>{(day.activity_moving_duration_s / 3600).toFixed(1)}</strong>h
            </span>
          )}
          {day.sleep_total_s != null && (
            <span>
              <strong>{(day.sleep_total_s / 3600).toFixed(1)}</strong>h sleep
            </span>
          )}
        </p>
      )}

      {activities.isLoading && <p>Loading…</p>}
      {activities.isError && <p role="alert">Could not load activities.</p>}
      {activities.data && activities.data.items.length === 0 && (
        <p>No activities recorded for this day.</p>
      )}

      <ul>
        {activities.data?.items.map((activity) => (
          <li key={activity.id} className="card">
            <Link to={`/activities/${activity.id}`}>
              {activity.sport}
              {activity.name ? ` — ${activity.name}` : ""}
            </Link>
            {activity.distance_m != null && ` · ${(activity.distance_m / 1000).toFixed(1)} km`}
            {activity.duration_s != null && ` · ${Math.round(activity.duration_s / 60)} min`}
          </li>
        ))}
      </ul>

      <section className="card">
        <h2>Notes</h2>
        <NotesPanel entityType="day" entityId={date} />
      </section>
    </main>
  );
}
