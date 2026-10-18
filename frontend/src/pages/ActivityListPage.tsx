// The activity list:
// activities grouped by local_date, each day getting a slim wellness strip (sleep, resting HR,
// steps -- all already available from GET /health/dashboard and GET /sleep, no new endpoint)
// above that day's ActivityCards.
import { Link, useLocation, useSearch } from "wouter";

import { flagParam, parseFlagChoice, type FlagChoices, type FlagKey } from "../activityFlagFilter";

import { useActivities, useActivityRoutes, useHealthDashboard, useSleep } from "../api/queries";
import { ActivitiesTabs } from "../components/ActivitiesTabs";
import { ActivityCard } from "../components/ActivityCard";
import { ActivityFlagFilters } from "../components/ActivityFlagFilters";
import { useDistanceFormat } from "../formatDistance";
import { calendarHomeHref } from "../calendarHome";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { MetricChip } from "../components/StatTile";
import { localIsoDate } from "../dateUtils";
import { valueForDate } from "../healthStats";
import { healthMetricStyle, KNOWN_SPORTS } from "../metricStyle";
import { usePersonalize } from "../PersonalizeContext";
import { groupByLocalDate } from "../yearStats";
import "../styles/activity-list.css";
import "../styles/compare.css";

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

interface ListView {
  sport: string;
  minDistance: string;
  maxDistance: string;
  flags: FlagChoices;
  offset: number;
}

function positiveInt(raw: string | null): number {
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : 0;
}

export function ActivityListPage() {
  const { default_view: defaultView } = usePersonalize();
  // The filter and page live in the URL, so opening an activity and coming back (or reloading)
  // keeps them. Changing either replaces the history entry instead of stacking one per click.
  const search = useSearch();
  const [location, navigate] = useLocation();
  const params = new URLSearchParams(search);
  const { unitLabel, displayToMeters } = useDistanceFormat();
  const view: ListView = {
    sport: params.get("sport") ?? "",
    // Kept as typed (in the athlete's own distance unit); converted to metres for the request.
    minDistance: params.get("min") ?? "",
    maxDistance: params.get("max") ?? "",
    flags: {
      race: parseFlagChoice(params.get("race")),
      virtual: parseFlagChoice(params.get("virtual")),
      commute: parseFlagChoice(params.get("commute")),
    },
    offset: positiveInt(params.get("offset")),
  };
  const { sport, offset } = view;
  // Any change goes back to the first page, except paging itself.
  const setView = (change: Partial<ListView>) => {
    const merged: ListView = { ...view, offset: 0, ...change };
    const next = new URLSearchParams();
    if (merged.sport) next.set("sport", merged.sport);
    if (merged.minDistance) next.set("min", merged.minDistance);
    if (merged.maxDistance) next.set("max", merged.maxDistance);
    for (const key of ["race", "virtual", "commute"] as const) {
      if (merged.flags[key]) next.set(key, merged.flags[key]);
    }
    if (merged.offset > 0) next.set("offset", String(merged.offset));
    const query = next.toString();
    navigate(query ? `${location}?${query}` : location, { replace: true });
  };
  const toMeters = (value: string) => {
    const n = Number(value);
    return value.trim() !== "" && Number.isFinite(n) && n >= 0 ? displayToMeters(n) : undefined;
  };
  const activities = useActivities({
    sport: sport || undefined,
    limit: PAGE_SIZE,
    offset,
    minDistanceM: toMeters(view.minDistance),
    maxDistanceM: toMeters(view.maxDistance),
    isRace: flagParam(view.flags.race),
    isVirtual: flagParam(view.flags.virtual),
    isCommute: flagParam(view.flags.commute),
  });

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
      <Link href={calendarHomeHref(defaultView)}>← Calendar</Link>
      <ActivitiesTabs />

      <form className="activity-filter">
        <label className="field">
          Sport
          <select
            className="input"
            value={sport}
            onChange={(e) => setView({ sport: e.target.value })}
          >
            <option value="">All sports</option>
            {KNOWN_SPORTS.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Distance from ({unitLabel})
          <input
            className="input"
            type="number"
            inputMode="decimal"
            min={0}
            step="any"
            value={view.minDistance}
            onChange={(e) => setView({ minDistance: e.target.value })}
          />
        </label>
        <label className="field">
          Distance to ({unitLabel})
          <input
            className="input"
            type="number"
            inputMode="decimal"
            min={0}
            step="any"
            value={view.maxDistance}
            onChange={(e) => setView({ maxDistance: e.target.value })}
          />
        </label>
        <ActivityFlagFilters
          values={view.flags}
          onChange={(key: FlagKey, value) => setView({ flags: { ...view.flags, [key]: value } })}
        />
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
            onClick={() => setView({ offset: Math.max(0, offset - PAGE_SIZE) })}
            disabled={offset === 0}
          >
            Previous
          </button>{" "}
          <button
            type="button"
            className="button"
            onClick={() => setView({ offset: offset + PAGE_SIZE })}
            disabled={offset + PAGE_SIZE >= activities.data.total}
          >
            Next
          </button>
        </p>
      )}
    </main>
  );
}
