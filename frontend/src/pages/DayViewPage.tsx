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
// Body Battery: a real per-reading chart, not a scalar tile -- GET /health/stream reads
// garmin.daily_body_battery.level, fetched live from Garmin's own get_body_battery() endpoint
// (see health/json_parser.py::parse_daily_body_battery_json's own docstring for why the
// previously-available daily-summary scalars were deliberately never charted: only 8 sparse
// named checkpoints, not a real curve). Only covers days from whenever this feature's own live
// fetch first ran onward -- a day before that simply shows no chart, same as any other absent
// metric.
import { lazy, Suspense } from "react";
import { Link } from "wouter";

import {
  useActivities,
  useActivityRoutes,
  useAllActivities,
  useCalendar,
  useFitness,
  useHealthDashboard,
  useHealthObservations,
  useHealthStream,
  useSleep,
} from "../api/queries";
import { ActivityCard } from "../components/ActivityCard";
import { BodyBatteryChart } from "../components/BodyBatteryChart";
import { DateNavigator } from "../components/DateNavigator";
import { DayViewActivityRoute } from "../components/DayViewActivityRoute";
import { Icon } from "../components/Icon";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { NotesPanel } from "../components/NotesPanel";
import { ScheduleWorkoutForm } from "../components/ScheduleWorkoutForm";
import { StatTile } from "../components/StatTile";
import { mondayOf, parseIsoDate } from "../dateUtils";
import { latestObservation, valueForDate } from "../healthStats";
import { healthMetricStyle } from "../metricStyle";
import { newAllTimePrs, personalRecords } from "../runningStats";
import "../styles/activity-list.css";
import "../styles/calendar.css";
import "../styles/running-stats.css";

const TRAINING_STATUS_KEY = "garmin.export.TrainingHistory.trainingStatus";
const OBSERVATION_KEYS = [TRAINING_STATUS_KEY];
const BODY_BATTERY_STREAM_KEY = "garmin.daily_body_battery.level";

// Code-split, not a static import -- the app-shell bundle is already right at
// vite-plugin-pwa's 2MB single-file precache limit (see ExerciseStepEditor.tsx's own
// preloadExerciseCatalog docstring for the exact same constraint biting once before), and this
// card is well below the fold, not needed for the page's first paint.
const PlannedRaceForm = lazy(() =>
  import("../components/PlannedRaceForm").then((m) => ({ default: m.PlannedRaceForm })),
);

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
  const bodyBatteryStream = useHealthStream(BODY_BATTERY_STREAM_KEY, date, true);

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

  // Steps and floors read here but rendered under Fitness & Form below, not Health -- both are
  // daily-activity totals, not wellness/vitals signals, which is what the Health section is for.
  const steps = valueForDate(
    health.data?.metrics.find((m) => m.logical_metric === "steps"),
    date,
  );
  const floors = valueForDate(
    health.data?.metrics.find((m) => m.logical_metric === "floors_ascended"),
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
  const weight = valueForDate(
    health.data?.metrics.find((m) => m.logical_metric === "weight_kg"),
    date,
  );
  const bodyFat = valueForDate(
    health.data?.metrics.find((m) => m.logical_metric === "body_fat_pct"),
    date,
  );

  const items = observations.data?.items ?? [];
  const trainingStatus = latestObservation(items, TRAINING_STATUS_KEY);

  const stepsStyle = healthMetricStyle("steps");
  const floorsStyle = healthMetricStyle("floors_ascended");
  const restingHrStyle = healthMetricStyle("resting_heart_rate");
  const hrvStyle = healthMetricStyle("hrv_nightly_average");
  const weightStyle = healthMetricStyle("weight_kg");
  const bodyFatStyle = healthMetricStyle("body_fat_pct");

  const hasFitnessTile = fitnessToday != null || steps != null || floors != null;
  const hasHealthTile =
    restingHr != null ||
    hrv != null ||
    sleepToday != null ||
    trainingStatus != null ||
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

      {activities.isLoading && <LoadingSpinner />}
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

      {hasFitnessTile && (
        <section className="card">
          <h2>Fitness &amp; Form</h2>
          <div className="stat-grid">
            {fitnessToday && (
              <>
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
              </>
            )}
            {steps != null && (
              <StatTile
                label="Steps"
                value={Math.round(steps).toLocaleString()}
                icon={stepsStyle.icon}
                tone={stepsStyle.tone}
              />
            )}
            {floors != null && (
              <StatTile
                label="Floors"
                value={floors.toFixed(0)}
                unit="m"
                icon={floorsStyle.icon}
                tone={floorsStyle.tone}
              />
            )}
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
            {trainingStatus?.value_text != null && (
              <StatTile
                label="Training status"
                value={trainingStatus.value_text.replace(/_/g, " ")}
                icon="trend"
                tone="power"
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

      {bodyBatteryStream.data && bodyBatteryStream.data.timestamps.length > 0 && (
        <section className="card">
          <h2>Body Battery</h2>
          <BodyBatteryChart
            points={bodyBatteryStream.data.timestamps.map((timestamp, i) => ({
              timestamp,
              level: bodyBatteryStream.data!.values[i]!,
            }))}
          />
        </section>
      )}

      <section className="card">
        <h2>Planned workout</h2>
        <ScheduleWorkoutForm localDate={date} />
      </section>

      <section className="card">
        <h2>Race</h2>
        <Suspense fallback={<LoadingSpinner size="sm" />}>
          <PlannedRaceForm localDate={date} />
        </Suspense>
      </section>

      <section className="card">
        <h2>Notes</h2>
        <NotesPanel entityType="day" entityId={date} showHeading={false} />
      </section>
    </main>
  );
}
