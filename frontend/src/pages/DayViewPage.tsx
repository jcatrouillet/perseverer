// Milestone D of docs/adr/0010-phase-6.1-frontend-design.md's plan: the day view combines that
// day's activities (ActivityCard, from Milestone B), a compact Fitness & Form tile, and health
// tiles -- all composed client-side from three already-existing endpoints (GET /fitness,
// GET /health/dashboard, GET /health/observations), no new backend endpoint.
//
// Training Readiness and Training Status aren't part of the health/dashboard logical-metric
// system (they're raw garmin.export.* observations, not a daily rollup) -- read directly via
// GET /health/observations, taking the day's *latest* reading rather than an average, since
// several arrive per day and "today's readiness" conventionally means the freshest one.
//
// Body Battery: included because real data was confirmed present in the archive first (a
// direct `SELECT DISTINCT metric_key ... LIKE '%body%batt%'` against the real DB, matching this
// project's standing practice of verifying against the real catalog rather than assuming
// Garmin's full widget set exists) -- it only actually covers Jan-Apr 2025 in this archive, so
// the tile simply doesn't appear outside that range, same as any other absent metric.
import { Link } from "wouter";

import {
  useActivities,
  useActivityRoutes,
  useAllActivities,
  useCalendar,
  useFitness,
  useHealthDashboard,
  useHealthObservations,
  useSleep,
} from "../api/queries";
import { ActivityCard } from "../components/ActivityCard";
import { DateNavigator } from "../components/DateNavigator";
import { DayViewActivityRoute } from "../components/DayViewActivityRoute";
import { Icon } from "../components/Icon";
import { NotesPanel } from "../components/NotesPanel";
import { StatTile } from "../components/StatTile";
import { mondayOf, parseIsoDate } from "../dateUtils";
import { latestObservation, valueForDate } from "../healthStats";
import { healthMetricStyle } from "../metricStyle";
import { newAllTimePrs, personalRecords } from "../runningStats";
import "../styles/activity-list.css";
import "../styles/calendar.css";
import "../styles/running-stats.css";

const READINESS_KEY = "garmin.export.TrainingReadinessDTO.score";
const TRAINING_STATUS_KEY = "garmin.export.TrainingHistory.trainingStatus";
const BODY_BATTERY_KEY = "garmin.daily_summary.bodyBatteryMostRecentValue";
const BODY_BATTERY_HIGH_KEY = "garmin.daily_summary.bodyBatteryHighestValue";
const BODY_BATTERY_LOW_KEY = "garmin.daily_summary.bodyBatteryLowestValue";
const OBSERVATION_KEYS = [
  READINESS_KEY,
  TRAINING_STATUS_KEY,
  BODY_BATTERY_KEY,
  BODY_BATTERY_HIGH_KEY,
  BODY_BATTERY_LOW_KEY,
];

export function DayViewPage({ date }: { date: string }) {
  const calendar = useCalendar(date, date);
  const activities = useActivities({ startDate: date, endDate: date, limit: 50 });
  // Unbounded all-time running history, for the "PBs set today" callout below -- see ADR 0011
  // decision 3 and RunningStats.tsx's allTimeRecords prop docstring.
  const allTimeRunning = useAllActivities({ sport: "running" });
  const fitness = useFitness(date, date);
  const health = useHealthDashboard(date, date);
  const sleep = useSleep(date, date);
  const observations = useHealthObservations(OBSERVATION_KEYS, date, date);

  const day = calendar.data?.days[0];
  const routes = useActivityRoutes((activities.data?.items ?? []).map((a) => a.id));
  const polylineById = new Map(
    (routes.data ?? [])
      .filter((r) => r.simplified_polyline != null)
      .map((r) => [r.id, r.simplified_polyline!]),
  );
  const dayRunning = activities.data?.items.filter((a) => a.sport === "running") ?? [];
  const newPrsToday = newAllTimePrs(
    personalRecords(dayRunning),
    personalRecords(allTimeRunning.data ?? []),
  );
  const weekStart = mondayOf(parseIsoDate(date));
  const year = weekStart.getUTCFullYear();
  const month = weekStart.getUTCMonth() + 1;

  const fitnessToday = fitness.data?.find((f) => f.local_date === date);
  const sleepToday = sleep.data?.find((s) => s.local_date === date);

  const steps = valueForDate(
    health.data?.metrics.find((m) => m.logical_metric === "steps"),
    date,
  );
  const restingHr = valueForDate(
    health.data?.metrics.find((m) => m.logical_metric === "resting_heart_rate"),
    date,
  );
  const hrv = valueForDate(
    health.data?.metrics.find((m) => m.logical_metric === "hrv_nightly_average"),
    date,
  );
  const stress = valueForDate(
    health.data?.metrics.find((m) => m.logical_metric === "stress_average"),
    date,
  );
  const weight = valueForDate(
    health.data?.metrics.find((m) => m.logical_metric === "weight_kg"),
    date,
  );
  const bodyFat = valueForDate(
    health.data?.metrics.find((m) => m.logical_metric === "body_fat_pct"),
    date,
  );

  const items = observations.data?.items ?? [];
  const readiness = latestObservation(items, READINESS_KEY);
  const trainingStatus = latestObservation(items, TRAINING_STATUS_KEY);
  const bodyBattery = latestObservation(items, BODY_BATTERY_KEY);
  const bodyBatteryHigh = latestObservation(items, BODY_BATTERY_HIGH_KEY);
  const bodyBatteryLow = latestObservation(items, BODY_BATTERY_LOW_KEY);
  const bodyBatteryRange =
    bodyBatteryHigh?.value_num != null && bodyBatteryLow?.value_num != null
      ? `${Math.round(bodyBatteryLow.value_num)}–${Math.round(bodyBatteryHigh.value_num)} range`
      : null;

  const stepsStyle = healthMetricStyle("steps");
  const restingHrStyle = healthMetricStyle("resting_heart_rate");
  const hrvStyle = healthMetricStyle("hrv_nightly_average");
  const stressStyle = healthMetricStyle("stress_average");
  const weightStyle = healthMetricStyle("weight_kg");
  const bodyFatStyle = healthMetricStyle("body_fat_pct");

  const hasHealthTile =
    steps != null ||
    restingHr != null ||
    hrv != null ||
    stress != null ||
    sleepToday != null ||
    readiness != null ||
    trainingStatus != null ||
    bodyBattery != null ||
    weight != null ||
    bodyFat != null;

  return (
    <main>
      <DateNavigator year={year} month={month} selectedDate={date} />
      <nav>
        <Link href={`/calendar/week/${weekStart.toISOString().slice(0, 10)}`}>← Week</Link>
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
        </p>
      )}

      {newPrsToday.length > 0 && (
        <p className="running-records__new-prs">
          <Icon name="trophy" /> {newPrsToday.length} all-time PR
          {newPrsToday.length === 1 ? "" : "s"} set today: {newPrsToday.map((r) => r.label).join(", ")}
        </p>
      )}

      {activities.isLoading && <p>Loading…</p>}
      {activities.isError && <p role="alert">Could not load activities.</p>}
      {activities.data && activities.data.items.length === 0 && (
        <p>No activities recorded for this day.</p>
      )}
      <div className="activity-day-group__list">
        {activities.data?.items.map((activity) => (
          <ActivityCard
            key={activity.id}
            activity={activity}
            iconSize="large"
            encodedPolyline={polylineById.get(activity.id)}
            // Only attempt the per-activity high-tier stream fetch for an activity that
            // actually has a route at all -- reuses the same routes batch fetch above (already
            // needed for the static-thumbnail fallback) as the gate, rather than every card
            // firing a stream request for e.g. a GPS-less strength-training session.
            animatedRoute={
              polylineById.has(activity.id) ? (
                <DayViewActivityRoute activityId={activity.id} />
              ) : undefined
            }
          />
        ))}
      </div>

      {fitnessToday && (
        <section className="card">
          <h2>Fitness &amp; Form</h2>
          <div className="stat-grid">
            <StatTile
              label="Fitness (CTL)"
              value={fitnessToday.ctl.toFixed(1)}
              icon="trend"
              tone="elevation"
              hero
            />
            <StatTile
              label="Fatigue (ATL)"
              value={fitnessToday.atl.toFixed(1)}
              icon="bolt"
              tone="load"
              hero
            />
            <StatTile
              label="Form (TSB)"
              value={fitnessToday.tsb.toFixed(1)}
              icon="gauge"
              tone="pace"
              hero
            />
          </div>
        </section>
      )}

      {hasHealthTile && (
        <section className="card">
          <h2>Health</h2>
          <div className="stat-grid">
            {sleepToday?.total_sleep_s != null && (
              <StatTile
                label="Sleep"
                value={(sleepToday.total_sleep_s / 3600).toFixed(1)}
                unit="h"
                icon="moon"
                tone="cadence"
                hero
              />
            )}
            {sleepToday?.sleep_score != null && (
              <StatTile label="Sleep score" value={sleepToday.sleep_score} icon="moon" tone="cadence" />
            )}
            {steps != null && (
              <StatTile
                label="Steps"
                value={Math.round(steps).toLocaleString()}
                icon={stepsStyle.icon}
                tone={stepsStyle.tone}
                hero
              />
            )}
            {restingHr != null && (
              <StatTile
                label="Resting heart rate"
                value={Math.round(restingHr)}
                unit="bpm"
                icon={restingHrStyle.icon}
                tone={restingHrStyle.tone}
              />
            )}
            {hrv != null && (
              <StatTile
                label="HRV"
                value={hrv.toFixed(0)}
                unit="ms"
                icon={hrvStyle.icon}
                tone={hrvStyle.tone}
              />
            )}
            {stress != null && (
              <StatTile label="Stress" value={stress.toFixed(0)} icon={stressStyle.icon} tone={stressStyle.tone} />
            )}
            {readiness?.value_num != null && (
              <StatTile
                label="Training readiness"
                value={Math.round(readiness.value_num)}
                icon="trend"
                tone="power"
              />
            )}
            {trainingStatus?.value_text != null && (
              <StatTile
                label="Training status"
                value={trainingStatus.value_text.replace(/_/g, " ")}
                icon="trend"
                tone="power"
              />
            )}
            {bodyBattery?.value_num != null && (
              <StatTile
                label="Body Battery"
                value={Math.round(bodyBattery.value_num)}
                meta={bodyBatteryRange}
                icon="battery"
                tone="cadence"
              />
            )}
            {weight != null && (
              <StatTile
                label="Weight"
                value={weight.toFixed(1)}
                unit="kg"
                icon={weightStyle.icon}
                tone={weightStyle.tone}
              />
            )}
            {bodyFat != null && (
              <StatTile
                label="Body fat"
                value={bodyFat.toFixed(1)}
                unit="%"
                icon={bodyFatStyle.icon}
                tone={bodyFatStyle.tone}
              />
            )}
          </div>
        </section>
      )}

      <section className="card">
        <h2>Notes</h2>
        <NotesPanel entityType="day" entityId={date} />
      </section>
    </main>
  );
}
