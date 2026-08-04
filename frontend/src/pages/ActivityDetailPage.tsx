import { Link } from "wouter";

import { useActivity, useActivityStream } from "../api/queries";
import { NotesPanel } from "../components/NotesPanel";
import { StreamChart } from "../components/StreamChart";

export function ActivityDetailPage({ id }: { id: string }) {
  const activity = useActivity(id);
  const stream = useActivityStream(id, activity.data?.stream_available ?? false);

  if (activity.isLoading) return <p>Loading…</p>;
  if (activity.isError || !activity.data) return <p role="alert">Activity not found.</p>;

  const a = activity.data;

  return (
    <main>
      <Link to="/activities">← Activities</Link>
      <h1>
        {a.sport}
        {a.name ? ` — ${a.name}` : ""}
      </h1>
      <p>{new Date(a.start_time_utc).toLocaleString()}</p>
      <ul>
        {a.distance_m != null && <li>Distance: {(a.distance_m / 1000).toFixed(2)} km</li>}
        {a.duration_s != null && <li>Duration: {Math.round(a.duration_s / 60)} min</li>}
        {a.elevation_gain_m != null && <li>Elevation gain: {a.elevation_gain_m.toFixed(0)} m</li>}
        {a.calories != null && <li>Calories: {a.calories.toFixed(0)}</li>}
        {a.device && (
          <li>
            Device: {a.device.manufacturer} {a.device.product}
          </li>
        )}
      </ul>

      {a.laps.length > 0 && (
        <section>
          <h2>Laps</h2>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Duration</th>
                <th>Distance</th>
                <th>Avg HR</th>
              </tr>
            </thead>
            <tbody>
              {a.laps.map((lap) => (
                <tr key={lap.lap_index}>
                  <td>{lap.lap_index + 1}</td>
                  <td>{lap.duration_s != null ? `${Math.round(lap.duration_s)}s` : "—"}</td>
                  <td>
                    {lap.distance_m != null ? `${(lap.distance_m / 1000).toFixed(2)} km` : "—"}
                  </td>
                  <td>{lap.avg_hr ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {a.stream_available && (
        <section>
          <h2>Stream</h2>
          {stream.isLoading && <p>Loading stream…</p>}
          {stream.isError && <p role="alert">Could not load stream data.</p>}
          {stream.data && <StreamChart stream={stream.data} />}
        </section>
      )}

      <NotesPanel entityType="activity" entityId={id} />
    </main>
  );
}
