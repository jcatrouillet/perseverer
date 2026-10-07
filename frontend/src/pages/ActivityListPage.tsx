// The activity list:
// activities grouped by local_date, each day getting a slim wellness strip (sleep, resting HR,
// steps -- all already available from GET /health/dashboard and GET /sleep, no new endpoint)
// above that day's ActivityCards.
import { useState } from "react";
import { Link } from "wouter";

import { useActivities, useActivityRoutes, useHealthDashboard, useSleep } from "../api/queries";
import { ActivityCard } from "../components/ActivityCard";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { MetricChip } from "../components/StatTile";
import { localIsoDate } from "../dateUtils";
import { valueForDate } from "../healthStats";
import { healthMetricStyle, KNOWN_SPORTS } from "../metricStyle";
import { groupByLocalDate } from "../yearStats";
import "../styles/activity-list.css";

const PAGE_SIZE = 50;
const RESTING_HR_STYLE = healthMetricStyle("resting_heart_rate");
const STEPS_STYLE = healthMetricStyle("steps");

function formatDayHeading(localDate: string): { date: string; weekday: string } {
  const d = new Date(`${localDate}T00:00:00Z`);
  const date = d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
  const weekday = d.toLocaleDateString("en-US", { weekday: "long", timeZone: "UTC" });
  return { date, weekday };
}

export function ActivityListPage() {
  const [sport, setSport] = useState("");
  const [offset, setOffset] = useState(0);
  const activities = useActivities({ sport: sport || undefined, limit: PAGE_SIZE, offset });

  const groups = groupByLocalDate(activities.data?.items ?? []);
  // One batch request for the whole page's routes rather than one per card -- see
  // useActivityRoutes' own docstring.
  const routes = useActivityRoutes((activities.data?.items ?? []).map((a) => a.id));
  const polylineById = new Map(
    (routes.data ?? [])
      .filter((r) => r.simplified_polyline != null)
      .map((r) => [r.id, r.simplified_polyline!]),
  );
  const sortedDates = groups
    .map((g) => g.localDate)
    .slice()
    .sort();
  const today = localIsoDate();
  const rangeStart = sortedDates[0] ?? today;
  const rangeEnd = sortedDates[sortedDates.length - 1] ?? today;

  // Supplementary to the activity list itself -- fetched for whatever date range the current
  // page of activities actually spans, same client-aggregation pattern YearView/MonthView use
  // rather than a new backend endpoint.
  const health = useHealthDashboard(rangeStart, rangeEnd);
  const sleep = useSleep(rangeStart, rangeEnd);

  return (
    <main>
      <h1>Activities</h1>
      <Link href="/">← Calendar</Link>

      <form className="activity-filter">
        <label className="field">
          Sport
          <select
            className="input"
            value={sport}
            onChange={(e) => {
              setSport(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All sports</option>
            {KNOWN_SPORTS.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
      </form>

      {activities.isLoading && <LoadingSpinner />}
      {activities.isError && <p role="alert">Could not load activities.</p>}
      {activities.data && groups.length === 0 && <p>No activities found.</p>}

      {groups.map((group) => {
        const { date, weekday } = formatDayHeading(group.localDate);
        const restingHr = valueForDate(
          health.data?.metrics.find((m) => m.logical_metric === "resting_heart_rate"),
          group.localDate,
        );
        const steps = valueForDate(
          health.data?.metrics.find((m) => m.logical_metric === "steps"),
          group.localDate,
        );
        const sleepSession = sleep.data?.find((s) => s.local_date === group.localDate);

        return (
          <section className="activity-day-group" key={group.localDate}>
            <div className="activity-day-group__header">
              <span className="activity-day-group__date">{date}</span>
              <span className="activity-day-group__weekday">{weekday}</span>
              <div className="activity-day-group__wellness">
                {sleepSession?.total_sleep_s != null && (
                  <MetricChip
                    label={`${(sleepSession.total_sleep_s / 3600).toFixed(1)}h sleep`}
                    icon="moon"
                    tone="cadence"
                  />
                )}
                {restingHr != null && (
                  <MetricChip
                    label={`${Math.round(restingHr)} bpm resting`}
                    icon={RESTING_HR_STYLE.icon}
                    tone={RESTING_HR_STYLE.tone}
                  />
                )}
                {steps != null && (
                  <MetricChip
                    label={`${Math.round(steps).toLocaleString()} steps`}
                    icon={STEPS_STYLE.icon}
                    tone={STEPS_STYLE.tone}
                  />
                )}
              </div>
            </div>
            <div className="activity-day-group__list">
              {group.activities.map((activity) => (
                <ActivityCard
                  key={activity.id}
                  activity={activity}
                  encodedPolyline={polylineById.get(activity.id)}
                />
              ))}
            </div>
          </section>
        );
      })}

      {activities.data && (
        <p>
          <button
            type="button"
            className="button"
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            disabled={offset === 0}
          >
            Previous
          </button>{" "}
          <button
            type="button"
            className="button"
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
