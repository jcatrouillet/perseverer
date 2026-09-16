// Categorized stat tiles for the activity detail page (Milestone C). Every value here comes
// from `ActivityDetail.metrics` (the full per-activity EAV list the API already returns) or the
// core `ActivityDetail` fields -- no new backend endpoint. Each section (and each tile within
// it) renders only when the underlying field is actually present for this activity: a walk has
// no Power section, an indoor ride has no Elevation section, and a device without a footpod
// has no Running Dynamics section. Nothing here is estimated or backfilled.
import type { ActivityDetail } from "../api/types";
import { metricValue, metricValueAliased } from "../activityMetrics";
import { boulderingRoutes, climbSummary, formatGrade, isBoulderingActivity } from "../boulderingRoutes";
import { useDistanceFormat } from "../formatDistance";
import {
  effectiveDurationS,
  formatDurationHM,
  formatMinPerKm,
  isPaceSport,
} from "../runningStats";
import { StatTile } from "./StatTile";

/** Distance & time, Heart rate, and the `afterHeartRate` slot (the route map) -- the part of the
 * stats grid ActivityDetailPage renders full-width, above the narrower two-column row that holds
 * the remaining sections. Split out from the rest so the route map's width doesn't get squeezed
 * by a side column that has nothing to do with it (the "fastest for this distance" table only
 * makes sense alongside stat tiles, not alongside the map). */
export function ActivityStatsGridPrimary({
  activity,
  afterHeartRate,
}: {
  activity: ActivityDetail;
  /** Rendered immediately after the Heart rate section -- a slot rather than this component
   * reaching out to fetch/render the route map itself, since ActivityStatsGrid's whole job is
   * "one categorized read of `activity.metrics`", not knowing about the separate high-tier
   * stream fetch the route map needs. */
  afterHeartRate?: React.ReactNode;
}) {
  const durationS = effectiveDurationS(activity);
  const paceSport = isPaceSport(activity.sport);
  const { metersToDisplay, unitLabel, paceMinPerDisplayUnit, kmhToDisplay, speedUnitLabel } =
    useDistanceFormat();
  const totalDescent = metricValueAliased(activity.metrics, [
    "fit.session.total_descent",
    "strava.session.total_descent",
  ]);
  const hasHeartRate = activity.avg_hr_bpm != null || activity.max_hr_bpm != null;
  // Determines whether the Elevation column needs the wider `--wide` modifier (see
  // activity-detail.css's own comment on .activity-stats__row--compact) -- its default 320px
  // basis only fits 2 tiles side by side; a 3rd (gain + loss + max elevation, common for any
  // GPS hike with a barometric altimeter) wraps onto its own line without it.
  const elevationTileCount = [
    activity.elevation_gain_m != null,
    totalDescent != null,
    activity.max_altitude_m != null,
  ].filter(Boolean).length;
  const hasElevation = elevationTileCount > 0;
  const isBouldering = isBoulderingActivity(activity.sport, activity.sub_sport);
  // Bouldering has no distance/pace at all -- the tiles below already skip both via their own
  // `distance_m != null` guards, so the only bouldering-specific change needed here is the
  // heading text itself (there's genuinely no "distance" in this card once that's true).
  const climb = isBouldering ? climbSummary(boulderingRoutes(activity.splits)) : null;

  return (
    <div className="activity-stats">
      <div
        className={
          isBouldering ? "activity-stats__row activity-stats__row--compact" : "activity-stats__row"
        }
      >
        <div className="activity-stats__col">
          <h3>{isBouldering ? "Time & calories" : "Distance & time"}</h3>
          <div className="stat-grid">
            {activity.distance_m != null && (
              <StatTile
                label="Distance"
                value={metersToDisplay(activity.distance_m).toFixed(2)}
                unit={unitLabel}
                icon="route"
                tone="pace"
                hero
              />
            )}
            {durationS != null && (
              <StatTile label="Moving time" value={formatDurationHM(durationS)} icon="clock" tone="cadence" hero />
            )}
            {/* distance_m > 0, not just non-null -- see ActivityCard's identical guard. */}
            {activity.distance_m != null && activity.distance_m > 0 && durationS != null && durationS > 0 && (
              <StatTile
                label={paceSport ? "Avg pace" : "Avg speed"}
                value={
                  paceSport
                    ? formatMinPerKm(
                        paceMinPerDisplayUnit(durationS / (activity.distance_m / 1000)),
                      )
                    : kmhToDisplay(activity.distance_m / 1000 / (durationS / 3600)).toFixed(1)
                }
                unit={paceSport ? `/${unitLabel}` : speedUnitLabel}
                icon="gauge"
                tone="pace"
              />
            )}
            {activity.calories != null && (
              <StatTile label="Calories" value={activity.calories.toFixed(0)} unit="kcal" icon="flame" tone="load" />
            )}
          </div>
        </div>

        {climb && (
          <div className="activity-stats__col activity-stats__col--wide">
            <h3>Climb</h3>
            <div className="stat-grid">
              <StatTile
                label="Max completed grade"
                value={climb.maxCompletedGrade != null ? formatGrade(climb.maxCompletedGrade) : "—"}
                icon="mountain"
                tone="elevation"
                hero
              />
              <StatTile label="Routes" value={climb.routeCount} icon="route" tone="pace" hero />
              {climb.climbTimeS != null && (
                <StatTile
                  label="Climb time"
                  value={formatDurationHM(climb.climbTimeS)}
                  icon="clock"
                  tone="cadence"
                />
              )}
            </div>
          </div>
        )}
      </div>

      {(hasHeartRate || hasElevation) && (
        <div className="activity-stats__row activity-stats__row--compact">
          {hasHeartRate && (
            <div className="activity-stats__col">
              <h3>Heart rate</h3>
              <div className="stat-grid">
                {activity.avg_hr_bpm != null && (
                  <StatTile
                    label="Avg heart rate"
                    value={Math.round(activity.avg_hr_bpm)}
                    unit="bpm"
                    icon="heart"
                    tone="hr"
                    hero
                  />
                )}
                {activity.max_hr_bpm != null && (
                  <StatTile label="Max heart rate" value={Math.round(activity.max_hr_bpm)} unit="bpm" icon="heart" tone="hr" />
                )}
              </div>
            </div>
          )}

          {hasElevation && (
            <div
              className={
                elevationTileCount >= 3
                  ? "activity-stats__col activity-stats__col--wide"
                  : "activity-stats__col"
              }
            >
              <h3>Elevation</h3>
              <div className="stat-grid">
                {activity.elevation_gain_m != null && (
                  <StatTile label="Elevation gain" value={activity.elevation_gain_m.toFixed(0)} unit="m" icon="mountain" tone="elevation" />
                )}
                {totalDescent != null && (
                  <StatTile label="Elevation loss" value={totalDescent.toFixed(0)} unit="m" icon="mountain" tone="elevation" />
                )}
                {activity.max_altitude_m != null && (
                  <StatTile label="Max elevation" value={activity.max_altitude_m.toFixed(0)} unit="m" icon="mountain" tone="elevation" />
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {afterHeartRate}
    </div>
  );
}

/** Respiration through Hydration -- the part of the stats grid that shares a row with the
 * "fastest for this distance" side column on ActivityDetailPage. */
export function ActivityStatsGridSecondary({
  activity,
  afterTemperature,
}: {
  activity: ActivityDetail;
  /** Rendered right after the Temperature section (before Hydration) -- a slot for the same
   * reason ActivityStatsGridPrimary's afterHeartRate is: this component only knows about
   * `activity.metrics`, not the separate high-tier stream the pace variability chart needs. */
  afterTemperature?: React.ReactNode;
}) {
  const metrics = activity.metrics;

  const aerobicEffect = metricValue(metrics, "fit.session.total_training_effect");
  const anaerobicEffect = metricValue(metrics, "fit.session.total_anaerobic_training_effect");
  const avgPower = metricValue(metrics, "fit.session.avg_power");
  const maxPower = metricValue(metrics, "fit.session.max_power");
  const normalizedPower = metricValue(metrics, "fit.session.normalized_power");
  // FIT's avg/max_running_cadence are single-foot rates; Garmin Connect's own "avg/max cadence"
  // for a run is that figure doubled (confirmed against real data: session values of ~75-88
  // correspond to the conventional 150-176 spm runners actually see displayed).
  const avgRunningCadenceRaw = metricValue(metrics, "fit.session.avg_running_cadence");
  const maxRunningCadenceRaw = metricValue(metrics, "fit.session.max_running_cadence");
  const avgVerticalOscillation = metricValue(metrics, "fit.session.avg_vertical_oscillation");
  const avgStanceTime = metricValue(metrics, "fit.session.avg_stance_time");
  const avgStepLengthMm = metricValue(metrics, "fit.session.avg_step_length");
  const avgVerticalRatio = metricValue(metrics, "fit.session.avg_vertical_ratio");
  const avgTemp = metricValue(metrics, "fit.session.avg_temperature");
  const minTemp = metricValue(metrics, "fit.session.min_temperature");
  const maxTemp = metricValue(metrics, "fit.session.max_temperature");
  const avgRespiration = metricValue(metrics, "fit.session.enhanced_avg_respiration_rate");
  const maxRespiration = metricValue(metrics, "fit.session.enhanced_max_respiration_rate");
  const minRespiration = metricValue(metrics, "fit.session.enhanced_min_respiration_rate");

  const hasTrainingEffect =
    aerobicEffect != null ||
    anaerobicEffect != null ||
    activity.training_load != null ||
    activity.workout_rpe != null ||
    activity.vdot != null;
  const hasRunningDynamics =
    avgRunningCadenceRaw != null ||
    maxRunningCadenceRaw != null ||
    avgVerticalOscillation != null ||
    avgStanceTime != null ||
    avgStepLengthMm != null;
  const hasTemperature = avgTemp != null;
  const hasPower = avgPower != null || maxPower != null || normalizedPower != null;
  const hasRespiration = avgRespiration != null || maxRespiration != null;
  const hasHydration = activity.estimated_sweat_loss_ml != null;

  return (
    <div className="activity-stats">
      {hasRespiration && (
        <>
          <h3>Respiration</h3>
          <div className="stat-grid">
            {avgRespiration != null && (
              <StatTile label="Avg respiration" value={avgRespiration.toFixed(0)} unit="brpm" icon="pulse" tone="cadence" />
            )}
            {maxRespiration != null && (
              <StatTile label="Max respiration" value={maxRespiration.toFixed(0)} unit="brpm" icon="pulse" tone="cadence" />
            )}
            {minRespiration != null && (
              <StatTile label="Min respiration" value={minRespiration.toFixed(0)} unit="brpm" icon="pulse" tone="cadence" />
            )}
          </div>
        </>
      )}

      {hasTrainingEffect && (
        <>
          <h3>Training effect</h3>
          <div className="stat-grid">
            {aerobicEffect != null && (
              <StatTile label="Aerobic effect" value={aerobicEffect.toFixed(1)} icon="trend" tone="power" />
            )}
            {anaerobicEffect != null && (
              <StatTile label="Anaerobic effect" value={anaerobicEffect.toFixed(1)} icon="bolt" tone="power" />
            )}
            {activity.training_load != null && (
              <StatTile label="Training load" value={Math.round(activity.training_load)} icon="bolt" tone="load" />
            )}
            {activity.workout_rpe != null && (
              <StatTile label="Perceived effort" value={activity.workout_rpe.toFixed(1)} unit="RPE" icon="flame" tone="load" />
            )}
            {activity.vdot != null && (
              <StatTile label="VDOT" value={activity.vdot.toFixed(1)} icon="trend" tone="pace" />
            )}
          </div>
        </>
      )}

      {hasPower && (
        <>
          <h3>Power</h3>
          <div className="stat-grid">
            {avgPower != null && <StatTile label="Avg power" value={Math.round(avgPower)} unit="W" icon="bolt" tone="power" />}
            {maxPower != null && <StatTile label="Max power" value={Math.round(maxPower)} unit="W" icon="bolt" tone="power" />}
            {normalizedPower != null && (
              <StatTile label="Normalized power" value={Math.round(normalizedPower)} unit="W" icon="bolt" tone="power" />
            )}
          </div>
        </>
      )}

      {hasRunningDynamics && (
        <>
          <h3>Running dynamics</h3>
          <div className="stat-grid">
            {avgRunningCadenceRaw != null && (
              <StatTile
                label="Avg cadence"
                value={Math.round(avgRunningCadenceRaw * 2)}
                unit="spm"
                icon="steps"
                tone="cadence"
              />
            )}
            {maxRunningCadenceRaw != null && (
              <StatTile
                label="Max cadence"
                value={Math.round(maxRunningCadenceRaw * 2)}
                unit="spm"
                icon="steps"
                tone="cadence"
              />
            )}
            {avgStepLengthMm != null && (
              <StatTile label="Step length" value={(avgStepLengthMm / 10).toFixed(0)} unit="cm" icon="route" tone="cadence" />
            )}
            {avgStanceTime != null && (
              <StatTile label="Ground contact time" value={Math.round(avgStanceTime)} unit="ms" icon="clock" tone="cadence" />
            )}
            {avgVerticalOscillation != null && (
              <StatTile
                label="Vertical oscillation"
                value={avgVerticalOscillation.toFixed(1)}
                unit="mm"
                icon="trend"
                tone="cadence"
              />
            )}
            {avgVerticalRatio != null && (
              <StatTile label="Vertical ratio" value={avgVerticalRatio.toFixed(1)} unit="%" icon="trend" tone="cadence" />
            )}
          </div>
        </>
      )}

      {hasTemperature && (
        <>
          <h3>Temperature</h3>
          <div className="stat-grid">
            <StatTile label="Avg temperature" value={avgTemp!.toFixed(0)} unit="°C" icon="thermometer" tone="load" />
            {minTemp != null && maxTemp != null && (
              <StatTile
                label="Temperature range"
                value={`${minTemp.toFixed(0)}–${maxTemp.toFixed(0)}`}
                unit="°C"
                icon="thermometer"
                tone="load"
              />
            )}
          </div>
        </>
      )}

      {afterTemperature}

      {hasHydration && (
        <>
          <h3>Hydration</h3>
          <div className="stat-grid">
            {/* Not a stored per-activity field -- matched at read time to the nearest hydration-
                log entry logged shortly after this activity ended (see routers/activities.py::
                _estimated_sweat_loss_ml). Absent whenever nothing landed close enough to be
                confidently this activity's, same as every other conditional tile here. */}
            <StatTile
              label="Estimated sweat loss"
              value={(activity.estimated_sweat_loss_ml! / 1000).toFixed(2)}
              unit="L"
              icon="droplet"
              tone="pace"
            />
          </div>
        </>
      )}
    </div>
  );
}

/** The full grid in one call, Primary followed by Secondary -- for callers that don't need the
 * two-part split (e.g. existing tests exercising the whole grid at once). */
export function ActivityStatsGrid({
  activity,
  afterHeartRate,
  afterTemperature,
}: {
  activity: ActivityDetail;
  afterHeartRate?: React.ReactNode;
  afterTemperature?: React.ReactNode;
}) {
  return (
    <>
      <ActivityStatsGridPrimary activity={activity} afterHeartRate={afterHeartRate} />
      <ActivityStatsGridSecondary activity={activity} afterTemperature={afterTemperature} />
    </>
  );
}
