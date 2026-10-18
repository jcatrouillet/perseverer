// Run and ride comparison (/activities/compare?ids=...&sport=cycling): up to 8 activities of one
// sport side by side. They are found by title, distance, date or tag and added to the comparison;
// each takes a colour slot it keeps until removed (compareRuns.ts). The page shows each one's
// summary above charts with every activity in its own colour, on a shared distance or
// moving-time axis: pace, GAP, heart rate, cadence, elevation and power for runs; speed, power,
// heart rate, cadence and elevation for rides.
import { useState } from "react";
import { Link, useLocation, useSearch } from "wouter";

import { useActivitiesByIds, useActivitySearch, useActivityStreams } from "../api/queries";
import type { ActivitySummary } from "../api/types";
import { flagParam, NO_FLAGS, type FlagChoices } from "../activityFlagFilter";
import { ActivitiesTabs } from "../components/ActivitiesTabs";
import { ActivityFlagFilters } from "../components/ActivityFlagFilters";
import { CompareCharts, type ComparedRun } from "../components/CompareCharts";
import { LoadingSpinner } from "../components/LoadingSpinner";
import {
  addToSlots,
  averagePaceSPerKm,
  averageSpeedKmhOf,
  COMPARE_SPORTS,
  compareColor,
  compareHref,
  fastestSimilarRun,
  MAX_COMPARED,
  parseCompareSport,
  parseSlots,
  prepareRunSeries,
  removeFromSlots,
  similarDistanceBand,
  similarRideBand,
  slotsToParam,
  type CompareAxis,
  type CompareSport,
} from "../compareRuns";
import { useDistanceFormat } from "../formatDistance";
import { formatClockDuration } from "../runningStats";
import "../styles/compare.css";

function formatDate(localDate: string | null): string {
  if (!localDate) return "—";
  return new Date(`${localDate}T00:00:00Z`).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

function runTitle(run: ActivitySummary, sport: CompareSport): string {
  return run.name?.trim() || (sport === "running" ? "Run" : "Ride");
}

function SelectedRun({
  run,
  slot,
  sport,
  onRemove,
}: {
  run: ActivitySummary;
  slot: number;
  sport: CompareSport;
  onRemove: () => void;
}) {
  const { formatDistance, kmhToDisplay, speedUnitLabel } = useDistanceFormat();
  const speed = averageSpeedKmhOf(run);
  const moving = run.moving_duration_s ?? run.duration_s;
  return (
    <li
      className="compare-run"
      style={{ "--run-color": compareColor(slot) } as React.CSSProperties}
    >
      <div className="compare-run__head">
        <Link href={`/activities/${run.id}`} className="compare-run__title">
          {runTitle(run, sport)}
        </Link>
        <button
          type="button"
          className="compare-run__remove"
          onClick={onRemove}
          aria-label={`Remove ${runTitle(run, sport)} from the comparison`}
          title="Remove from the comparison"
        >
          ×
        </button>
      </div>
      <div className="compare-run__date">{formatDate(run.local_date)}</div>
      <dl className="compare-run__stats">
        <div>
          <dt>Distance</dt>
          <dd>{run.distance_m != null ? formatDistance(run.distance_m, 2) : "—"}</dd>
        </div>
        <div>
          <dt>Moving time</dt>
          <dd>{moving != null ? formatClockDuration(moving) : "—"}</dd>
        </div>
        {sport === "cycling" && (
          <>
            <div>
              <dt>Avg speed</dt>
              <dd>{speed != null ? `${kmhToDisplay(speed).toFixed(1)} ${speedUnitLabel}` : "—"}</dd>
            </div>
            <div>
              <dt>Avg power</dt>
              <dd>{run.avg_power_w != null ? `${Math.round(run.avg_power_w)} W` : "—"}</dd>
            </div>
          </>
        )}
        <div>
          <dt>Load</dt>
          <dd>{run.training_load != null ? Math.round(run.training_load) : "—"}</dd>
        </div>
        {sport === "running" && (
          <div>
            <dt>VDOT</dt>
            <dd>{run.vdot != null ? run.vdot.toFixed(1) : "—"}</dd>
          </div>
        )}
      </dl>
    </li>
  );
}

/** How many similar-distance runs the suggestion looks through (the most recent ones). */
const SUGGESTION_POOL = 500;

/** With a single run selected: suggests the fastest run of the same whole distance (32.0 to
 * 32.99 km for a 32.02 km run) to compare it with. */
function SimilarRunSuggestion({
  selected,
  sport,
  onAdd,
}: {
  selected: ActivitySummary;
  sport: CompareSport;
  onAdd: (id: string) => void;
}) {
  const { formatDistance, formatPace, kmhToDisplay, speedUnitLabel, unit, unitLabel } =
    useDistanceFormat();
  const { one } = COMPARE_SPORTS[sport];
  const running = sport === "running";
  const band = selected.distance_m
    ? running
      ? similarDistanceBand(selected.distance_m, unit)
      : similarRideBand(selected.distance_m, unit)
    : null;
  // Always run the query (hooks can't be conditional); a run with no band asks for nothing useful
  // and gets no suggestion below.
  const similar = useActivitySearch(
    { minDistanceM: band?.lowM ?? 0, maxDistanceM: band?.highM ?? 0 },
    COMPARE_SPORTS[sport].apiSport,
    SUGGESTION_POOL,
  );
  if (band == null || !similar.data) return null;
  const suggestion = fastestSimilarRun(selected, similar.data.items, band, sport);
  if (suggestion == null) {
    return (
      <p className="chart-note compare-suggestion__none">
        No other {running ? "" : "about "}
        {band.whole} {unitLabel} {one} to compare with yet.
      </p>
    );
  }
  const { run, selectedIsFastest } = suggestion;
  const pace = averagePaceSPerKm(run);
  const speed = averageSpeedKmhOf(run);
  const distanceLabel = `${running ? "" : "about "}${band.whole} ${unitLabel}`;
  const partial = similar.data.total > similar.data.items.length;

  return (
    <div className="compare-suggestion">
      <p className="compare-suggestion__lead">
        {selectedIsFastest
          ? `This is your fastest ${distanceLabel} ${one}. Compare it with the next fastest:`
          : `Compare with your fastest ${distanceLabel} ${one}:`}
      </p>
      <div className="compare-suggestion__run">
        <Link href={`/activities/${run.id}`} className="compare-suggestion__title">
          {runTitle(run, sport)}
        </Link>
        <span className="compare-suggestion__meta">
          {formatDate(run.local_date)}
          {run.distance_m != null && ` · ${formatDistance(run.distance_m, 2)}`}
          {running && pace != null && ` · ${formatPace(pace)}`}
          {!running && speed != null && ` · ${kmhToDisplay(speed).toFixed(1)} ${speedUnitLabel}`}
        </span>
        <button type="button" className="button button--primary" onClick={() => onAdd(run.id)}>
          Add
        </button>
      </div>
      {partial && (
        <p className="chart-note">
          Among your {similar.data.items.length} most recent {COMPARE_SPORTS[sport].many} of that
          distance.
        </p>
      )}
    </div>
  );
}

function RunSearch({
  selected,
  full,
  sport,
  onAdd,
}: {
  selected: Set<string>;
  full: boolean;
  sport: CompareSport;
  onAdd: (id: string) => void;
}) {
  const { many } = COMPARE_SPORTS[sport];
  const { formatDistance, unitLabel, displayToMeters } = useDistanceFormat();
  const [q, setQ] = useState("");
  const [minDistance, setMinDistance] = useState("");
  const [maxDistance, setMaxDistance] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [flags, setFlags] = useState<FlagChoices>(NO_FLAGS);
  const toMeters = (value: string) => {
    const n = Number(value);
    return value.trim() !== "" && Number.isFinite(n) && n >= 0 ? displayToMeters(n) : undefined;
  };
  const results = useActivitySearch(
    {
      q: q.trim(),
      minDistanceM: toMeters(minDistance),
      maxDistanceM: toMeters(maxDistance),
      startDate,
      endDate,
      isRace: flagParam(flags.race),
      isVirtual: flagParam(flags.virtual),
      isCommute: flagParam(flags.commute),
    },
    COMPARE_SPORTS[sport].apiSport,
  );

  return (
    <section className="card compare-search">
      <h2>Add {many}</h2>
      <div className="compare-search__form">
        <label className="field compare-search__title">
          Title
          <input
            className="input"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={
              sport === "running" ? "e.g. tempo, long run" : "e.g. gran fondo, hill climb"
            }
          />
        </label>
        <label className="field">
          From ({unitLabel})
          <input
            className="input"
            type="number"
            inputMode="decimal"
            min={0}
            step="any"
            value={minDistance}
            onChange={(e) => setMinDistance(e.target.value)}
          />
        </label>
        <label className="field">
          To ({unitLabel})
          <input
            className="input"
            type="number"
            inputMode="decimal"
            min={0}
            step="any"
            value={maxDistance}
            onChange={(e) => setMaxDistance(e.target.value)}
          />
        </label>
        <label className="field">
          Date from
          <input
            className="input"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </label>
        <label className="field">
          Date to
          <input
            className="input"
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </label>
        <ActivityFlagFilters
          values={flags}
          onChange={(key, value) => setFlags((f) => ({ ...f, [key]: value }))}
        />
      </div>
      {full && (
        <p className="chart-note">
          {MAX_COMPARED} {many} selected: remove one to add another.
        </p>
      )}
      {results.isLoading && <LoadingSpinner />}
      {results.isError && <p role="alert">Could not search the runs.</p>}
      {results.data && results.data.items.length === 0 && (
        <p className="chart-note">No {COMPARE_SPORTS[sport].one} matches.</p>
      )}
      {results.data && results.data.items.length > 0 && (
        <ul className="compare-results">
          {results.data.items.map((run) => {
            const added = selected.has(run.id);
            return (
              <li key={run.id} className="compare-results__row">
                <span className="compare-results__title">{runTitle(run, sport)}</span>
                <span className="compare-results__meta">
                  {formatDate(run.local_date)}
                  {run.distance_m != null && ` · ${formatDistance(run.distance_m, 2)}`}
                </span>
                <button
                  type="button"
                  className="button"
                  disabled={added || full}
                  onClick={() => onAdd(run.id)}
                >
                  {added ? "Added" : "Add"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {results.data && results.data.total > results.data.items.length && (
        <p className="chart-note">
          Showing the {results.data.items.length} most recent of {results.data.total} matching{" "}
          {many}: narrow the search to find older ones.
        </p>
      )}
    </section>
  );
}

export function CompareRunsPage() {
  const search = useSearch();
  const [, setLocation] = useLocation();
  const params = new URLSearchParams(search);
  // One sport at a time: runs are only ever compared with runs, rides with rides.
  const sport = parseCompareSport(params.get("sport"));
  const copy = COMPARE_SPORTS[sport];
  const slots = parseSlots(params.get("ids"));
  const ids = slots.filter((id): id is string => id != null);
  const [axis, setAxis] = useState<CompareAxis>("distance");

  const setSlots = (next: (string | null)[]) => {
    setLocation(compareHref(sport, slotsToParam(next)), { replace: true });
  };

  const summaries = useActivitiesByIds(ids);
  const byId = new Map((summaries.data?.items ?? []).map((run) => [run.id, run]));
  const streams = useActivityStreams(ids);

  const compared: ComparedRun[] = [];
  ids.forEach((id, i) => {
    const series = streams[i]?.data ? prepareRunSeries(streams[i].data, sport) : null;
    const run = byId.get(id);
    if (series && run) {
      compared.push({ slot: slots.indexOf(id), label: runTitle(run, sport), series });
    }
  });
  const streamsLoading = streams.some((s) => s.isLoading);

  return (
    <main className="compare-page">
      <h1>Activities</h1>
      <ActivitiesTabs />

      <section className="card">
        <div className="compare-charts__header">
          <h2>Compare {copy.many}</h2>
          <div className="compare-axis" role="group" aria-label="Sport">
            {(Object.keys(COMPARE_SPORTS) as CompareSport[]).map((value) => (
              <button
                key={value}
                type="button"
                className={`compare-axis__option${sport === value ? " is-active" : ""}`}
                aria-pressed={sport === value}
                // A different sport starts a fresh comparison: its ids would not match anyway.
                onClick={() =>
                  sport !== value && setLocation(compareHref(value), { replace: true })
                }
              >
                {COMPARE_SPORTS[value].label}
              </button>
            ))}
          </div>
        </div>
        {ids.length === 0 ? (
          <p className="chart-note">
            Add up to {MAX_COMPARED} {copy.many} below to compare their{" "}
            {sport === "running"
              ? "pace, heart rate, cadence, elevation and power"
              : "speed, power, heart rate, cadence and elevation"}
            . Each {copy.one} gets its own colour.
          </p>
        ) : (
          <>
            {summaries.isLoading && <LoadingSpinner />}
            <ul className="compare-runs">
              {slots.map((id, slot) => {
                const run = id != null ? byId.get(id) : undefined;
                if (!id || !run) return null;
                return (
                  <SelectedRun
                    key={id}
                    run={run}
                    slot={slot}
                    sport={sport}
                    onRemove={() => setSlots(removeFromSlots(slots, id))}
                  />
                );
              })}
            </ul>
            {ids.length === 1 && byId.get(ids[0]!) && (
              <SimilarRunSuggestion
                selected={byId.get(ids[0]!)!}
                sport={sport}
                onAdd={(id) => setSlots(addToSlots(slots, id))}
              />
            )}
          </>
        )}
      </section>

      {ids.length > 0 && (
        <section className="card">
          <div className="compare-charts__header">
            <h2>Charts</h2>
            <div className="compare-axis" role="group" aria-label="Horizontal axis">
              {(["distance", "time"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`compare-axis__option${axis === value ? " is-active" : ""}`}
                  aria-pressed={axis === value}
                  onClick={() => setAxis(value)}
                >
                  {value === "distance" ? "Distance" : "Moving time"}
                </button>
              ))}
            </div>
          </div>
          {streamsLoading && <LoadingSpinner />}
          {!streamsLoading && compared.length === 0 && (
            <p className="chart-note">None of these {copy.many} has chart data.</p>
          )}
          {compared.length > 0 && <CompareCharts runs={compared} axis={axis} sport={sport} />}
        </section>
      )}

      <RunSearch
        selected={new Set(ids)}
        full={ids.length >= MAX_COMPARED}
        sport={sport}
        onAdd={(id) => setSlots(addToSlots(slots, id))}
      />
    </main>
  );
}
