import { useState } from "react";
import { Link } from "wouter";

import { useActivities } from "../api/queries";

const PAGE_SIZE = 50;

export function ActivityListPage() {
  const [sport, setSport] = useState("");
  const [offset, setOffset] = useState(0);
  const activities = useActivities({ sport: sport || undefined, limit: PAGE_SIZE, offset });

  return (
    <main>
      <h1>Activities</h1>
      <Link to="/">← Calendar</Link>
      <form>
        <label>
          Sport
          <input
            type="text"
            value={sport}
            onChange={(e) => {
              setSport(e.target.value);
              setOffset(0);
            }}
            placeholder="e.g. running"
          />
        </label>
      </form>

      {activities.isLoading && <p>Loading…</p>}
      {activities.isError && <p role="alert">Could not load activities.</p>}
      {activities.data && activities.data.items.length === 0 && <p>No activities found.</p>}

      <ul>
        {activities.data?.items.map((activity) => (
          <li key={activity.id}>
            <Link to={`/activities/${activity.id}`}>
              {activity.local_date ?? activity.start_time_utc.slice(0, 10)} — {activity.sport}
              {activity.name ? ` — ${activity.name}` : ""}
            </Link>
            {activity.distance_m != null && ` · ${(activity.distance_m / 1000).toFixed(1)} km`}
            {activity.duration_s != null && ` · ${Math.round(activity.duration_s / 60)} min`}
          </li>
        ))}
      </ul>

      {activities.data && (
        <p>
          <button
            type="button"
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            disabled={offset === 0}
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= activities.data.total}
          >
            Next
          </button>
        </p>
      )}
    </main>
  );
}
