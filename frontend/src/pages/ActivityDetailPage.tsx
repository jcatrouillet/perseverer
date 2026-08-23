// Milestone C of docs/adr/0010-phase-6.1-frontend-design.md's plan: multi-panel stream charts
// (ActivityCharts, replacing StreamChart's single-channel-with-a-selector), a categorized stats
// grid, a time-in-zone breakdown, and a styled laps table -- all built on the Milestone A/B
// design system (Icon, StatTile, sportStyle, tone colours).
import { useMemo, useState } from "react";
import { Link } from "wouter";

import { extractHrZones } from "../activityMetrics";
import {
  useActivity,
  useActivityClimbComparisons,
  useActivityComparisons,
  useActivityContext,
  useActivityInsights,
  useActivityLocation,
  useActivitySources,
  useActivityStream,
  useActivityWeather,
  useActivityWorkout,
  useAddClimbRoute,
  useDeleteClimbRoute,
  useHrZoneConfig,
  useSetClimbRouteStatus,
  useSetFuelingOverride,
  useSetNameOverride,
  useSetRaceOverride,
  useSetSportOverride,
  useSplitActivitySource,
} from "../api/queries";
import { ActivityCharts } from "../components/ActivityCharts";
import { ActivityComparisonTable } from "../components/ActivityComparisonTable";
import { ActivityContextStrip } from "../components/ActivityContextStrip";
import { ActivityFastestTable } from "../components/ActivityFastestTable";
import { ActivityFueling } from "../components/ActivityFueling";
import { ActivityInsightsPanel } from "../components/ActivityInsightsPanel";
import { ActivityNameCorrection } from "../components/ActivityNameCorrection";
import { ActivityRoute, buildRouteData } from "../components/ActivityRoute";
import { ActivitySourcesPanel } from "../components/ActivitySourcesPanel";
import { ActivitySportCorrection } from "../components/ActivitySportCorrection";
import {
  ActivityStatsGridPrimary,
  ActivityStatsGridSecondary,
} from "../components/ActivityStatsGrid";
import { ActivityWeather } from "../components/ActivityWeather";
import { BoulderingRoutesTable } from "../components/BoulderingRoutesTable";
import { ChartFullscreen } from "../components/ChartFullscreen";
import { ClimbComparisonTable } from "../components/ClimbComparisonTable";
import { ClimbGradeChart } from "../components/ClimbGradeChart";
import { Icon } from "../components/Icon";
import { NotesPanel } from "../components/NotesPanel";
import { PaceVariabilityChart } from "../components/PaceVariabilityChart";
import { TimeInZoneChart } from "../components/TimeInZoneChart";
import { boulderingRoutes, gradeBreakdownFromRoutes, isBoulderingActivity } from "../boulderingRoutes";
import { sportStyle } from "../metricStyle";
import { computePaceVariability } from "../paceVariability";
import {
  effectiveDurationS,
  formatClockDuration,
  formatPaceMinPerKm,
  isPaceSport,
  isRunningSport,
  localTimeLabel,
} from "../runningStats";
import { computeSplitsAtInterval } from "../splits";
import {
  expandWorkoutSteps,
  formatStepDurationLabel,
  labelForIntensity,
  targetPaceRangeLabel,
} from "../workoutSteps";
import { displayActivityName, displaySport } from "../yearStats";
import "../styles/activity-detail.css";

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
  // Below the Temperature section (ActivityStatsGridSecondary's own afterTemperature slot), not
  // beside the map -- computed here rather than inside ActivityRoute.tsx since that component
  // only knows about the route map/splits table, not the stats grid it needs to slot into. 100m
  // segments (not the Splits table's own whole-km rows) so the ring shows real pace texture
  // within each kilometre rather than smoothing it away -- see paceVariability.ts.
  const paceVariability = useMemo(() => {
    if (!routeStream.data || activity.data == null) return null;
    if (!isRunningSport(displaySport(activity.data))) return null;
    const route = buildRouteData(routeStream.data);
    const segments = computeSplitsAtInterval(route.distanceM, route.elapsedS, 100);
    return computePaceVariability(segments);
  }, [routeStream.data, activity.data]);
  const context = useActivityContext(id);
  const runInsights = useActivityInsights(
    id,
    activity.data != null && isRunningSport(activity.data.sport),
  );
  const comparisons = useActivityComparisons(
    id,
    activity.data != null && isRunningSport(activity.data.sport),
  );
  const climbComparisons = useActivityClimbComparisons(
    id,
    activity.data != null && isBoulderingActivity(activity.data.sport, activity.data.sub_sport),
  );
  const weather = useActivityWeather(id, activity.data?.route?.start_lat != null);
  const location = useActivityLocation(id, activity.data?.route?.start_lat != null);
  const workout = useActivityWorkout(id);
  const hrZoneConfig = useHrZoneConfig();
  const sources = useActivitySources(id);
  const split = useSplitActivitySource(id);
  const sportOverride = useSetSportOverride(id);
  const raceOverride = useSetRaceOverride(id);
  const nameOverride = useSetNameOverride(id);
  const fuelingOverride = useSetFuelingOverride(id);
  const setClimbRouteStatus = useSetClimbRouteStatus(id);
  const addClimbRoute = useAddClimbRoute(id);
  const deleteClimbRoute = useDeleteClimbRoute(id);

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
  // Shared with ActivityCard (yearStats.ts::displayActivityName) so the list/day-view cards and
  // this page's own header always agree on when to show the FIT session's own name vs. fall back
  // to Garmin's structured Workout Builder name (a.workout_name) vs. show nothing at all.
  const displayName = displayActivityName(a);

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
              <span
                className="activity-detail__race-badge"
                title="Marked as a race in Garmin Connect"
              >
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
        {location.data?.available && location.data.location_name && (
          <> · {location.data.location_name}</>
        )}
        {a.device &&
          a.device.manufacturer &&
          ` · ${a.device.manufacturer} ${a.device.product ?? ""}`}
      </p>
      {((weather.data && weather.data.available) || paceVariability) && (
        <div className="activity-detail__weather-pace-row">
          {weather.data && <ActivityWeather weather={weather.data} />}
          {paceVariability && (
            <div className="activity-detail__pace-variability">
              <ChartFullscreen
                as="h3"
                className="activity-detail__weather-heading"
                title="Pace variability"
              >
                <PaceVariabilityChart result={paceVariability} />
              </ChartFullscreen>
            </div>
          )}
        </div>
      )}
      {isRunningSport(sport) && (
        <ActivityFueling
          carbohydratesG={a.carbohydrates_g}
          sodiumMg={a.sodium_mg}
          onSubmit={(values) => fuelingOverride.mutate(values)}
          isSubmitting={fuelingOverride.isPending}
          isError={fuelingOverride.isError}
        />
      )}

      {/* Insights sit beside Distance & time / Heart rate only -- not beside the map, which is
          why the map is rendered as its own full-width block below this row rather than passed
          in as ActivityStatsGridPrimary's afterHeartRate slot (that would put it inside the same
          flex row as the insights column, stretching the side column down the map's height too). */}
      <div className="activity-detail__layout">
        <div className="activity-detail__main-col">
          <ActivityStatsGridPrimary activity={a} />
        </div>
        {runInsights.data && runInsights.data.length > 0 && (
          <div className="activity-detail__insights-col">
            <ActivityInsightsPanel insights={runInsights.data} />
          </div>
        )}
      </div>

      {routeStream.data && <ActivityRoute stream={routeStream.data} sport={sport} />}

      <div className="activity-detail__layout">
        <div className="activity-detail__main-col">
          <ActivityStatsGridSecondary activity={a} />
        </div>
        {context.data && (
          <div className="activity-detail__fastest-col">
            <ActivityFastestTable
              fastest={context.data.fastest}
              sport={sport}
              currentActivityId={id}
            />
          </div>
        )}
      </div>

      {context.data && (
        <ActivityContextStrip context={context.data} sport={sport} currentActivityId={id} />
      )}

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

      {a.laps.length > 0 && !isBoulderingActivity(a.sport, a.sub_sport) && (
        <section className="card">
          <h2>Intervals</h2>
          <div className="table-scroll">
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
                  const expectedPace = expectedStep
                    ? (targetPaceRangeLabel(expectedStep, sport) ?? "—")
                    : "—";
                  const intervalName = expectedStep
                    ? labelForIntensity(expectedStep.intensity)
                    : "—";
                  return (
                    <tr
                      key={lap.lap_index}
                      className={
                        hoveredLapIndex === i ? "intervals-table__row--hovered" : undefined
                      }
                      onMouseEnter={() => setHoveredLapIndex(i)}
                      onMouseLeave={() => setHoveredLapIndex(null)}
                    >
                      <td>{lap.lap_index + 1}</td>
                      {showExpectedColumns && <td>{intervalName}</td>}
                      <td>
                        {effectiveLapDuration != null
                          ? formatClockDuration(effectiveLapDuration)
                          : "—"}
                      </td>
                      {showExpectedColumns && <td>{expectedDurationOrDistance}</td>}
                      <td>
                        {lap.distance_m != null ? `${(lap.distance_m / 1000).toFixed(2)} km` : "—"}
                      </td>
                      <td>
                        {effectiveLapDuration != null &&
                        lap.distance_m != null &&
                        lap.distance_m > 0
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
          </div>
        </section>
      )}

      {isBoulderingActivity(a.sport, a.sub_sport) && (
        <>
          <BoulderingRoutesTable
            splits={a.splits}
            onSetStatus={(splitIndex, result) =>
              setClimbRouteStatus.mutate({ splitIndex, result })
            }
            onAddRoute={(grade, result) => addClimbRoute.mutate({ grade, result })}
            onDeleteRoute={(splitIndex) => deleteClimbRoute.mutate(splitIndex)}
            isSaving={
              setClimbRouteStatus.isPending || addClimbRoute.isPending || deleteClimbRoute.isPending
            }
            isError={
              setClimbRouteStatus.isError || addClimbRoute.isError || deleteClimbRoute.isError
            }
          />
          {a.splits.length > 0 && (
            <section className="card">
              <h2>Routes Climbed</h2>
              <ClimbGradeChart gradeBreakdown={gradeBreakdownFromRoutes(boulderingRoutes(a.splits))} />
            </section>
          )}
          {climbComparisons.data && (
            <ClimbComparisonTable activity={a} comparisons={climbComparisons.data} />
          )}
        </>
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

      {comparisons.data && (
        <ActivityComparisonTable
          activity={a}
          comparisons={comparisons.data}
          sport={sport}
        />
      )}

      <section className="card">
        <h2>Notes</h2>
        <NotesPanel entityType="activity" entityId={id} showHeading={false} />
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
