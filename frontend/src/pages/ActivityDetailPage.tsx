// Milestone C of docs/adr/0010-phase-6.1-frontend-design.md's plan: multi-panel stream charts
// (ActivityCharts, replacing StreamChart's single-channel-with-a-selector), a categorized stats
// grid, a time-in-zone breakdown, and a styled laps table -- all built on the Milestone A/B
// design system (Icon, StatTile, sportStyle, tone colours).
import { Link } from "wouter";

import { extractHrZones } from "../activityMetrics";
import { useActivity, useActivityContext, useActivityStream, useActivityWeather } from "../api/queries";
import { ActivityCharts } from "../components/ActivityCharts";
import { ActivityContextStrip } from "../components/ActivityContextStrip";
import { ActivityRoute } from "../components/ActivityRoute";
import { ActivityStatsGrid } from "../components/ActivityStatsGrid";
import { ActivityWeather } from "../components/ActivityWeather";
import { Icon } from "../components/Icon";
import { NotesPanel } from "../components/NotesPanel";
import { TimeInZoneChart } from "../components/TimeInZoneChart";
import { sportStyle } from "../metricStyle";
import { formatClockDuration, formatPaceMinPerKm, isPaceSport, localTimeLabel } from "../runningStats";
import { displaySport } from "../yearStats";
import "../styles/activity-detail.css";

export function ActivityDetailPage({ id }: { id: string }) {
  const activity = useActivity(id);
  const stream = useActivityStream(id, activity.data?.stream_available ?? false, "medium");
  // A separate, higher-resolution fetch just for the route map + per-km splits below -- those
  // need per-km precision the multi-panel charts' "medium" tier (1000 points) doesn't give, but
  // there's no reason to pay that cost for the charts too, so it's fetched independently rather
  // than bumping the shared `stream` query's tier.
  const routeStream = useActivityStream(id, activity.data?.stream_available ?? false, "high");
  const context = useActivityContext(id);
  const weather = useActivityWeather(id, activity.data?.route?.start_lat != null);

  if (activity.isLoading) return <p>Loading…</p>;
  if (activity.isError || !activity.data) return <p role="alert">Activity not found.</p>;

  const a = activity.data;
  const sport = displaySport(a);
  const style = sportStyle(sport);
  const paceSport = isPaceSport(sport);
  // HR zones are a training-load concept that's meaningful for running and cycling; showing it
  // for e.g. strength training or yoga would just be noise even on the rare activity that has a
  // stray zone metric. Also guards against the empty-card case: a zone metric key can be present
  // (cataloged) with no actual seconds recorded, which extractHrZones already treats as "nothing
  // to show" for the chart itself, but the section wrapper needs to know that too.
  const hrZones = extractHrZones(a.metrics);
  const showTimeInZone =
    (sport === "running" || sport === "cycling") &&
    hrZones != null &&
    hrZones.some((z) => z.seconds > 0);

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
      {weather.data && <ActivityWeather weather={weather.data} />}

      <ActivityStatsGrid
        activity={a}
        afterHeartRate={routeStream.data && <ActivityRoute stream={routeStream.data} sport={sport} />}
      />

      {context.data && (
        <ActivityContextStrip context={context.data} sport={sport} currentActivityId={id} />
      )}

      {showTimeInZone && (
        <section className="card">
          <h2>Time in zones</h2>
          <TimeInZoneChart metrics={a.metrics} />
        </section>
      )}

      {a.laps.length > 0 && (
        <section className="card">
          <h2>Intervals</h2>
          <table className="intervals-table">
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
                  <td>{lap.duration_s != null ? formatClockDuration(lap.duration_s) : "—"}</td>
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
