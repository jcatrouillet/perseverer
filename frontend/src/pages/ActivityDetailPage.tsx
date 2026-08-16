// Milestone C of docs/adr/0010-phase-6.1-frontend-design.md's plan: multi-panel stream charts
// (ActivityCharts, replacing StreamChart's single-channel-with-a-selector), a categorized stats
// grid, a time-in-zone breakdown, and a styled laps table -- all built on the Milestone A/B
// design system (Icon, StatTile, sportStyle, tone colours).
import { useState } from "react";
import { Link } from "wouter";

import { extractHrZones } from "../activityMetrics";
import {
  useActivity,
  useActivityContext,
  useActivitySources,
  useActivityStream,
  useActivityWeather,
  useActivityWorkout,
  useHrZoneConfig,
  useSetNameOverride,
  useSetRaceOverride,
  useSetSportOverride,
  useSplitActivitySource,
} from "../api/queries";
import { ActivityCharts } from "../components/ActivityCharts";
import { ActivityContextStrip } from "../components/ActivityContextStrip";
import { ActivityFastestTable } from "../components/ActivityFastestTable";
import { ActivityNameCorrection } from "../components/ActivityNameCorrection";
import { ActivityRoute } from "../components/ActivityRoute";
import { ActivitySourcesPanel } from "../components/ActivitySourcesPanel";
import { ActivitySportCorrection } from "../components/ActivitySportCorrection";
import { ActivityStatsGridPrimary, ActivityStatsGridSecondary } from "../components/ActivityStatsGrid";
import { ActivityWeather } from "../components/ActivityWeather";
import { Icon } from "../components/Icon";
import { NotesPanel } from "../components/NotesPanel";
import { TimeInZoneChart } from "../components/TimeInZoneChart";
import { sportStyle } from "../metricStyle";
import {
  effectiveDurationS,
  formatClockDuration,
  formatPaceMinPerKm,
  isPaceSport,
  localTimeLabel,
} from "../runningStats";
import {
  expandWorkoutSteps,
  formatStepDurationLabel,
  labelForIntensity,
  targetPaceRangeLabel,
} from "../workoutSteps";
import { displaySport } from "../yearStats";
import "../styles/activity-detail.css";

// The literal device-generated default `activity.name` for a sport, confirmed as the single
// overwhelmingly dominant value in the real archive (e.g. "Run" on 628 of this athlete's running
// activities, "Walk" on 201 walks) -- not a fuzzy "looks generic" guess. Sports with no single
// dominant default (cycling, training, rowing, ...) are deliberately absent, so displayName below
// always just uses activity.name for those.
const GENERIC_DEFAULT_NAME_BY_SPORT: Record<string, string> = {
  running: "Run",
  walking: "Walk",
  hiking: "Hike",
  alpine_skiing: "Ski",
  snowshoeing: "Snowshoe",
};

export function ActivityDetailPage({ id }: { id: string }) {
  // Hovering an Intervals-table row highlights that same lap's time range across every
  // ActivityCharts panel -- mirrors SplitsTable/ActivityRouteMap's existing hover-highlight
  // pattern for per-km splits, just for laps instead.
  const [hoveredLapIndex, setHoveredLapIndex] = useState<number | null>(null);
  const activity = useActivity(id);
  const stream = useActivityStream(id, activity.data?.stream_available ?? false, "medium");
  // A separate, higher-resolution fetch just for the route map + per-km splits below -- those
  // need per-km precision the multi-panel charts' "medium" tier (1000 points) doesn't give, but
  // there's no reason to pay that cost for the charts too, so it's fetched independently rather
  // than bumping the shared `stream` query's tier.
  const routeStream = useActivityStream(id, activity.data?.stream_available ?? false, "high");
  const context = useActivityContext(id);
  const weather = useActivityWeather(id, activity.data?.route?.start_lat != null);
  const workout = useActivityWorkout(id);
  const hrZoneConfig = useHrZoneConfig();
  const sources = useActivitySources(id);
  const split = useSplitActivitySource(id);
  const sportOverride = useSetSportOverride(id);
  const raceOverride = useSetRaceOverride(id);
  const nameOverride = useSetNameOverride(id);

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
  // All four boundaries or none -- see hr_zones.py::compute_hr_zone_boundaries, which only ever
  // derives them together from the athlete's three reference values.
  const configuredZoneBoundaries: [number, number, number, number] | null =
    hrZoneConfig.data?.zone1_high_bpm != null &&
    hrZoneConfig.data.zone2_high_bpm != null &&
    hrZoneConfig.data.zone3_high_bpm != null &&
    hrZoneConfig.data.zone4_high_bpm != null
      ? [
          hrZoneConfig.data.zone1_high_bpm,
          hrZoneConfig.data.zone2_high_bpm,
          hrZoneConfig.data.zone3_high_bpm,
          hrZoneConfig.data.zone4_high_bpm,
        ]
      : null;
  const heartRateStream = stream.data?.series.heart_rate ?? null;
  const hasComputableStreamZones =
    configuredZoneBoundaries != null &&
    heartRateStream != null &&
    heartRateStream.some((v) => v != null);
  const showTimeInZone =
    (sport === "running" || sport === "cycling") &&
    ((hrZones != null && hrZones.some((z) => z.seconds > 0)) || hasComputableStreamZones);
  // The Intervals table's "Expected" columns only make sense when this activity actually has a
  // pre-planned workout -- skipped entirely, not just blank, when there isn't one, same as the
  // chart's own workout overlay.
  const expandedWorkoutSteps =
    workout.data != null && paceSport ? expandWorkoutSteps(workout.data.steps) : [];
  const showExpectedColumns = expandedWorkoutSteps.length > 0;
  // Garmin's structured Workout Builder name (e.g. "W11 Sat - Easy Shakeout") is a deliberately
  // athlete/plan-given title, and reads far more usefully at the top of the page than the FIT
  // session's own generic device default -- confirmed against the real archive: the single
  // dominant (by far) activity.name value for these sports is exactly this bare word (e.g. "Run"
  // 628 times for running, "Walk" 201 times for walking). Only used as a fallback when
  // activity.name is still that generic default (or empty) -- once the athlete corrects the name
  // via "Not the right title? Fix it" below, activity.name is no longer that literal default, so
  // the correction always wins over the workout's own name from here on.
  const genericDefaultName = GENERIC_DEFAULT_NAME_BY_SPORT[sport];
  const displayName =
    a.name == null || a.name === genericDefaultName ? (workout.data?.name ?? a.name) : a.name;

  return (
    <main>
      <Link href="/activities">← Activities</Link>

      <div className="activity-detail__header">
        <span className={`icon-chip icon-chip--lg tone-${style.tone}`}>
          <Icon name={style.icon} />
        </span>
        <div className="activity-detail__title">
          <h1 className="activity-detail__sport">
            {sport.replace(/_/g, " ")}
            {displayName ? ` — ${displayName}` : ""}
            {a.is_race && (
              <span className="activity-detail__race-badge" title="Marked as a race in Garmin Connect">
                <Icon name="trophy" /> Race
              </span>
            )}
          </h1>
          <div className="activity-detail__correction-row">
            <ActivitySportCorrection
              currentSport={sport}
              onSubmit={(newSport) => sportOverride.mutate({ sport: newSport })}
              isSubmitting={sportOverride.isPending}
              isError={sportOverride.isError}
            />
            {/* Garmin's own eventTypeId is the only automatic race signal (see
                garmin_activity_summary.py) -- it only reflects whether the athlete flagged the
                activity as a race inside the Garmin Connect app itself, so a real race never
                flagged there has no other signal to derive it from. This lets the athlete
                correct that directly rather than relying on a heuristic that can't see it. */}
            <button
              type="button"
              className="activity-detail__sport-fix-btn"
              disabled={raceOverride.isPending}
              onClick={() => raceOverride.mutate({ is_race: !a.is_race })}
            >
              {a.is_race ? "Not a race?" : "Mark as a race"}
            </button>
            {raceOverride.isError && (
              <span role="alert" className="activity-detail__sport-fix-error">
                Couldn't save that correction.
              </span>
            )}
            <ActivityNameCorrection
              currentName={a.name}
              onSubmit={(newName) => nameOverride.mutate({ name: newName })}
              isSubmitting={nameOverride.isPending}
              isError={nameOverride.isError}
            />
          </div>
        </div>
      </div>
      <p className="activity-detail__meta">
        {a.local_date ?? a.start_time_utc.slice(0, 10)} · {localTimeLabel(a)}
        {a.device && a.device.manufacturer && ` · ${a.device.manufacturer} ${a.device.product ?? ""}`}
      </p>
      {weather.data && <ActivityWeather weather={weather.data} />}

      <ActivityStatsGridPrimary
        activity={a}
        afterHeartRate={routeStream.data && <ActivityRoute stream={routeStream.data} sport={sport} />}
      />

      <div className="activity-detail__layout">
        <div className="activity-detail__main-col">
          <ActivityStatsGridSecondary activity={a} />
        </div>
        {context.data && (
          <div className="activity-detail__fastest-col">
            <ActivityFastestTable fastest={context.data.fastest} sport={sport} currentActivityId={id} />
          </div>
        )}
      </div>

      {context.data && <ActivityContextStrip context={context.data} sport={sport} currentActivityId={id} />}

      {showTimeInZone && (
        <section className="card">
          <h2>Time in zones</h2>
          <TimeInZoneChart
            metrics={a.metrics}
            heartRateStream={heartRateStream}
            timestamps={stream.data?.timestamps}
            configuredZoneBoundaries={configuredZoneBoundaries}
          />
        </section>
      )}

      {a.laps.length > 0 && (
        <section className="card">
          <h2>Intervals</h2>
          <table className="intervals-table">
            <thead>
              <tr>
                <th>#</th>
                {showExpectedColumns && <th>Interval</th>}
                <th>Duration</th>
                {showExpectedColumns && (
                  <th>
                    Exp. duration
                    <br />
                    or distance
                  </th>
                )}
                <th>Distance</th>
                <th>{paceSport ? "Pace" : "Speed"}</th>
                {showExpectedColumns && <th>Expected pace</th>}
                <th>Avg HR</th>
                <th>Max HR</th>
              </tr>
            </thead>
            <tbody>
              {a.laps.map((lap, i) => {
                // moving_duration_s (FIT's total_timer_time) excludes any device pause within
                // the lap; duration_s (total_elapsed_time) doesn't. A lap paused mid-interval
                // otherwise shows a hugely inflated duration and a nonsense pace -- confirmed
                // against a real recovery lap: 1008s elapsed vs 90s timer across a genuine
                // ~15min pause. Falls back to duration_s for laps backfilled before this field
                // existed (or a source, like TCX, with no separate pause-excluded field at all).
                const effectiveLapDuration = lap.moving_duration_s ?? lap.duration_s;
                // The device creates one lap per executed workout step, aligned by position
                // (see ActivityCharts.tsx's own workoutBands for the same convention, confirmed
                // against a real structured-workout FIT file) -- expandedWorkoutSteps[i] is this
                // lap's own planned step, if the activity has a workout at all.
                const expectedStep = expandedWorkoutSteps[i];
                // formatStepDurationLabel already fills in whichever the step's own planned
                // constraint actually is -- "15m"/"75s" for a time-based step, "1km" for a
                // distance-based one (see its own docstring) -- so one column covers both
                // rather than a duration column that's blank for every distance-based rep.
                const expectedDurationOrDistance = expectedStep
                  ? (formatStepDurationLabel(expectedStep) ?? "—")
                  : "—";
                const expectedPace = expectedStep ? (targetPaceRangeLabel(expectedStep, sport) ?? "—") : "—";
                const intervalName = expectedStep ? labelForIntensity(expectedStep.intensity) : "—";
                return (
                  <tr
                    key={lap.lap_index}
                    className={hoveredLapIndex === i ? "intervals-table__row--hovered" : undefined}
                    onMouseEnter={() => setHoveredLapIndex(i)}
                    onMouseLeave={() => setHoveredLapIndex(null)}
                  >
                    <td>{lap.lap_index + 1}</td>
                    {showExpectedColumns && <td>{intervalName}</td>}
                    <td>
                      {effectiveLapDuration != null ? formatClockDuration(effectiveLapDuration) : "—"}
                    </td>
                    {showExpectedColumns && <td>{expectedDurationOrDistance}</td>}
                    <td>{lap.distance_m != null ? `${(lap.distance_m / 1000).toFixed(2)} km` : "—"}</td>
                    <td>
                      {effectiveLapDuration != null && lap.distance_m != null && lap.distance_m > 0
                        ? paceSport
                          ? `${formatPaceMinPerKm(effectiveLapDuration, lap.distance_m)} /km`
                          : `${(lap.distance_m / 1000 / (effectiveLapDuration / 3600)).toFixed(1)} km/h`
                        : "—"}
                    </td>
                    {showExpectedColumns && <td>{expectedPace}</td>}
                    <td>{lap.avg_hr ?? "—"}</td>
                    <td>{lap.max_hr ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      {a.stream_available && (
        <section>
          <h2>Charts</h2>
          {stream.isLoading && <p>Loading stream…</p>}
          {stream.isError && <p role="alert">Could not load stream data.</p>}
          {stream.data && (
            <ActivityCharts
              stream={stream.data}
              laps={a.laps}
              sport={sport}
              distanceM={a.distance_m}
              durationS={effectiveDurationS(a)}
              highlightLapIndex={hoveredLapIndex}
              workout={workout.data}
            />
          )}
        </section>
      )}

      <section className="card">
        <h2>Notes</h2>
        <NotesPanel entityType="activity" entityId={id} />
      </section>

      {sources.data && (
        <ActivitySourcesPanel
          sources={sources.data}
          onSplit={(linkId) => split.mutate(linkId)}
          isSplitting={split.isPending}
          splitError={split.isError}
          splitSuccess={split.isSuccess}
        />
      )}
    </main>
  );
}
