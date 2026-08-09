// Milestone C of docs/adr/0010-phase-6.1-frontend-design.md's plan: multi-panel stream charts
// (ActivityCharts, replacing StreamChart's single-channel-with-a-selector), a categorized stats
// grid, a time-in-zone breakdown, and a styled laps table -- all built on the Milestone A/B
// design system (Icon, StatTile, sportStyle, tone colours).
import { Link } from "wouter";

import { useActivity, useActivityContext, useActivityStream } from "../api/queries";
import { ActivityCharts } from "../components/ActivityCharts";
import { ActivityContextStrip } from "../components/ActivityContextStrip";
import { ActivityStatsGrid } from "../components/ActivityStatsGrid";
import { Icon } from "../components/Icon";
import { NotesPanel } from "../components/NotesPanel";
import { TimeInZoneChart } from "../components/TimeInZoneChart";
import { sportStyle } from "../metricStyle";
import { formatPaceMinPerKm, isPaceSport, localTimeLabel } from "../runningStats";
import { displaySport } from "../yearStats";
import "../styles/activity-detail.css";

export function ActivityDetailPage({ id }: { id: string }) {
  const activity = useActivity(id);
  const stream = useActivityStream(id, activity.data?.stream_available ?? false, "medium");
  const context = useActivityContext(id);

  if (activity.isLoading) return <p>Loading…</p>;
  if (activity.isError || !activity.data) return <p role="alert">Activity not found.</p>;

  const a = activity.data;
  const sport = displaySport(a);
  const style = sportStyle(sport);
  const paceSport = isPaceSport(sport);

  return (
    <main>
      <Link href="/activities">← Activities</Link>

      <div className="activity-detail__header">
        <span className={`icon-chip tone-${style.tone}`}>
          <Icon name={style.icon} />
        </span>
        <div className="activity-detail__title">
          <h1 className="activity-detail__sport">
            {sport.replace(/_/g, " ")}
            {a.name ? ` — ${a.name}` : ""}
          </h1>
        </div>
      </div>
      <p className="activity-detail__meta">
        {a.local_date ?? a.start_time_utc.slice(0, 10)} · {localTimeLabel(a)}
        {a.device && a.device.manufacturer && ` · ${a.device.manufacturer} ${a.device.product ?? ""}`}
      </p>

      <ActivityStatsGrid activity={a} />

      {context.data && (
        <ActivityContextStrip context={context.data} sport={sport} currentActivityId={id} />
      )}

      {a.metrics.some((m) => m.metric_key.startsWith("fit.time_in_zone.")) && (
        <section className="card">
          <h2>Time in zones</h2>
          <TimeInZoneChart metrics={a.metrics} />
        </section>
      )}

      {a.laps.length > 0 && (
        <section className="card">
          <h2>Laps</h2>
          <table className="laps-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Duration</th>
                <th>Distance</th>
                <th>{paceSport ? "Pace" : "Speed"}</th>
                <th>Avg HR</th>
                <th>Max HR</th>
              </tr>
            </thead>
            <tbody>
              {a.laps.map((lap) => (
                <tr key={lap.lap_index}>
                  <td>{lap.lap_index + 1}</td>
                  <td>{lap.duration_s != null ? `${Math.round(lap.duration_s)}s` : "—"}</td>
                  <td>{lap.distance_m != null ? `${(lap.distance_m / 1000).toFixed(2)} km` : "—"}</td>
                  <td>
                    {lap.duration_s != null && lap.distance_m != null && lap.distance_m > 0
                      ? paceSport
                        ? `${formatPaceMinPerKm(lap.duration_s, lap.distance_m)} /km`
                        : `${(lap.distance_m / 1000 / (lap.duration_s / 3600)).toFixed(1)} km/h`
                      : "—"}
                  </td>
                  <td>{lap.avg_hr ?? "—"}</td>
                  <td>{lap.max_hr ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {a.stream_available && (
        <section>
          <h2>Charts</h2>
          {stream.isLoading && <p>Loading stream…</p>}
          {stream.isError && <p role="alert">Could not load stream data.</p>}
          {stream.data && <ActivityCharts stream={stream.data} laps={a.laps} sport={sport} />}
        </section>
      )}

      <section className="card">
        <h2>Notes</h2>
        <NotesPanel entityType="activity" entityId={id} />
      </section>
    </main>
  );
}
