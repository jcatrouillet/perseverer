// Run comparison (/activities/compare?ids=...): up to 8 runs side by side. Runs are found by
// title, distance or date and added to the comparison; each takes a colour slot it keeps until
// removed (compareRuns.ts). The page shows each run's summary (title, date, distance, moving
// time, load, VDOT) above charts of pace, GAP, heart rate, cadence, elevation and power with
// every run in its own colour, on a shared distance or moving-time axis.
import { useState } from "react";
import { Link, useLocation, useSearch } from "wouter";

import { useActivitiesByIds, useActivitySearch, useActivityStreams } from "../api/queries";
import type { ActivitySummary } from "../api/types";
import { ActivitiesTabs } from "../components/ActivitiesTabs";
import { CompareCharts, type ComparedRun } from "../components/CompareCharts";
import { LoadingSpinner } from "../components/LoadingSpinner";
import {
  addToSlots,
  averagePaceSPerKm,
  compareColor,
  fastestSimilarRun,
  MAX_COMPARED,
  parseSlots,
  prepareRunSeries,
  removeFromSlots,
  similarDistanceBand,
  slotsToParam,
  type CompareAxis,
} from "../compareRuns";
import { useDistanceFormat } from "../formatDistance";
import { formatClockDuration } from "../runningStats";
import "../styles/compare.css";

const RUNNING = "running";

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

function runTitle(run: ActivitySummary): string {
  return run.name?.trim() || "Run";
}

function SelectedRun({
  run,
  slot,
  onRemove,
}: {
  run: ActivitySummary;
  slot: number;
  onRemove: () => void;
}) {
  const { formatDistance } = useDistanceFormat();
  const moving = run.moving_duration_s ?? run.duration_s;
  return (
    <li
      className="compare-run"
      style={{ "--run-color": compareColor(slot) } as React.CSSProperties}
    >
      <div className="compare-run__head">
        <Link href={`/activities/${run.id}`} className="compare-run__title">
          {runTitle(run)}
        </Link>
        <button
          type="button"
          className="compare-run__remove"
          onClick={onRemove}
          aria-label={`Remove ${runTitle(run)} from the comparison`}
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
        <div>
          <dt>Load</dt>
          <dd>{run.training_load != null ? Math.round(run.training_load) : "—"}</dd>
        </div>
        <div>
          <dt>VDOT</dt>
          <dd>{run.vdot != null ? run.vdot.toFixed(1) : "—"}</dd>
        </div>
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
  onAdd,
}: {
  selected: ActivitySummary;
  onAdd: (id: string) => void;
}) {
  const { formatDistance, formatPace, unit, unitLabel } = useDistanceFormat();
  const band = selected.distance_m ? similarDistanceBand(selected.distance_m, unit) : null;
  // Always run the query (hooks can't be conditional); a run with no band asks for nothing useful
  // and gets no suggestion below.
  const similar = useActivitySearch(
    { minDistanceM: band?.lowM ?? 0, maxDistanceM: band?.highM ?? 0 },
    RUNNING,
    SUGGESTION_POOL,
  );
  if (band == null || !similar.data) return null;
  const suggestion = fastestSimilarRun(selected, similar.data.items, band);
  if (suggestion == null) {
    return (
      <p className="chart-note compare-suggestion__none">
        No other {band.whole} {unitLabel} run to compare with yet.
      </p>
    );
  }
  const { run, selectedIsFastest } = suggestion;
  const pace = averagePaceSPerKm(run);
  const distanceLabel = `${band.whole} ${unitLabel}`;
  const partial = similar.data.total > similar.data.items.length;

  return (
    <div className="compare-suggestion">
      <p className="compare-suggestion__lead">
        {selectedIsFastest
          ? `This is your fastest ${distanceLabel} run. Compare it with the next fastest:`
          : `Compare with your fastest ${distanceLabel} run:`}
      </p>
      <div className="compare-suggestion__run">
        <Link href={`/activities/${run.id}`} className="compare-suggestion__title">
          {runTitle(run)}
        </Link>
        <span className="compare-suggestion__meta">
          {formatDate(run.local_date)}
          {run.distance_m != null && ` · ${formatDistance(run.distance_m, 2)}`}
          {pace != null && ` · ${formatPace(pace)}`}
        </span>
        <button type="button" className="button button--primary" onClick={() => onAdd(run.id)}>
          Add
        </button>
      </div>
      {partial && (
        <p className="chart-note">
          Among your {similar.data.items.length} most recent runs of that distance.
        </p>
      )}
    </div>
  );
}

function RunSearch({
  selected,
  full,
  onAdd,
}: {
  selected: Set<string>;
  full: boolean;
  onAdd: (id: string) => void;
}) {
  const { formatDistance, unitLabel, displayToMeters } = useDistanceFormat();
  const [q, setQ] = useState("");
  const [minDistance, setMinDistance] = useState("");
  const [maxDistance, setMaxDistance] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
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
    },
    RUNNING,
  );

  return (
    <section className="card compare-search">
      <h2>Add runs</h2>
      <div className="compare-search__form">
        <label className="field compare-search__title">
          Title
          <input
            className="input"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. tempo, long run"
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
      </div>
      {full && (
        <p className="chart-note">{MAX_COMPARED} runs selected: remove one to add another.</p>
      )}
      {results.isLoading && <LoadingSpinner />}
      {results.isError && <p role="alert">Could not search the runs.</p>}
      {results.data && results.data.items.length === 0 && (
        <p className="chart-note">No run matches.</p>
      )}
      {results.data && results.data.items.length > 0 && (
        <ul className="compare-results">
          {results.data.items.map((run) => {
            const added = selected.has(run.id);
            return (
              <li key={run.id} className="compare-results__row">
                <span className="compare-results__title">{runTitle(run)}</span>
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
          Showing the {results.data.items.length} most recent of {results.data.total} matching runs:
          narrow the search to find older ones.
        </p>
      )}
    </section>
  );
}

export function CompareRunsPage() {
  const search = useSearch();
  const [, setLocation] = useLocation();
  const slots = parseSlots(new URLSearchParams(search).get("ids"));
  const ids = slots.filter((id): id is string => id != null);
  const [axis, setAxis] = useState<CompareAxis>("distance");

  const setSlots = (next: (string | null)[]) => {
    const param = slotsToParam(next);
    setLocation(param ? `/activities/compare?ids=${param}` : "/activities/compare", {
      replace: true,
    });
  };

  const summaries = useActivitiesByIds(ids);
  const byId = new Map((summaries.data?.items ?? []).map((run) => [run.id, run]));
  const streams = useActivityStreams(ids);

  const compared: ComparedRun[] = [];
  ids.forEach((id, i) => {
    const series = streams[i]?.data ? prepareRunSeries(streams[i].data) : null;
    const run = byId.get(id);
    if (series && run) compared.push({ slot: slots.indexOf(id), label: runTitle(run), series });
  });
  const streamsLoading = streams.some((s) => s.isLoading);

  return (
    <main className="compare-page">
      <h1>Activities</h1>
      <ActivitiesTabs />

      <section className="card">
        <h2>Compare runs</h2>
        {ids.length === 0 ? (
          <p className="chart-note">
            Add up to {MAX_COMPARED} runs below to compare their pace, heart rate, cadence,
            elevation and power. Each run gets its own colour.
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
                    onRemove={() => setSlots(removeFromSlots(slots, id))}
                  />
                );
              })}
            </ul>
            {ids.length === 1 && byId.get(ids[0]!) && (
              <SimilarRunSuggestion
                selected={byId.get(ids[0]!)!}
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
            <p className="chart-note">None of these runs has chart data.</p>
          )}
          {compared.length > 0 && <CompareCharts runs={compared} axis={axis} />}
        </section>
      )}

      <RunSearch
        selected={new Set(ids)}
        full={ids.length >= MAX_COMPARED}
        onAdd={(id) => setSlots(addToSlots(slots, id))}
      />
    </main>
  );
}
