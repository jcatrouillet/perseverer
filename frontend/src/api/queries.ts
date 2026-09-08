// One useQuery/useMutation hook per REST endpoint (src/perseverer/api/routers/*.py). Note
// mutations invalidate both the calendar and the activity/day's own note list, since a note
// can be created from either the calendar page or the activity detail page.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiGet, apiPatch, apiPost, apiPostForm, apiPut } from "./client";
import type {
  ActivityComparisonsOut,
  ActivityContextOut,
  ActivityDetail,
  ActivityFuelingOut,
  ActivityLocationOut,
  ActivityMapPointOut,
  ActivityMergePreviewOut,
  ActivityNameOverrideOut,
  ActivityPaceBandsOut,
  ActivityRouteOut,
  ActivitySourcesOut,
  ActivitySplitOut,
  ActivityRaceOverrideOut,
  ActivitySportOverrideOut,
  ActivitySummary,
  ActivityWeatherOut,
  ActivityWorkoutOut,
  AthleteProfileIn,
  AthleteProfileOut,
  CalendarFeedStatusOut,
  CalendarFeedUrlOut,
  CalendarResponse,
  ChangePasswordIn,
  ChangePasswordOut,
  ClimbComparisonsOut,
  ClimbingSummaryOut,
  DuplicatePairOut,
  EufyLoginIn,
  EufyLoginOut,
  EufyStatusOut,
  FitnessDailyRollupOut,
  GarminAuthStatusOut,
  GarminLoginIn,
  GarminLoginOut,
  GoalOut,
  GoalProgressOut,
  HealthDashboardOut,
  HealthObservationOut,
  HealthStreamResponse,
  HrZoneConfigIn,
  HrZoneConfigOut,
  InsightOut,
  JobSource,
  JobStatusOut,
  JobTriggerOut,
  NoteCreate,
  NoteOut,
  PaceBandOut,
  Page,
  PeriodCalendarResponse,
  PerformanceDailyRollupOut,
  PlannedWorkoutListItemOut,
  PlannedWorkoutOut,
  PlannedWorkoutStepIn,
  RecurringWorkoutOut,
  RevokeShareOut,
  RunningLoadConfigIn,
  RunningLoadConfigOut,
  ShareLinkOut,
  SleepSessionOut,
  SplitOut,
  StreamResponse,
  TrimCandidateOut,
} from "./types";

interface ActivityFilters {
  startDate?: string;
  endDate?: string;
  sport?: string;
  limit?: number;
  offset?: number;
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export function useCalendar(startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["calendar", startDate, endDate],
    queryFn: () =>
      apiGet<CalendarResponse>(
        `/api/v1/calendar${buildQuery({ start_date: startDate, end_date: endDate })}`,
      ),
  });
}

export function useCalendarWeeks(startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["calendar-weeks", startDate, endDate],
    queryFn: () =>
      apiGet<PeriodCalendarResponse>(
        `/api/v1/calendar/weeks${buildQuery({ start_date: startDate, end_date: endDate })}`,
      ),
  });
}

export function useCalendarMonths(startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["calendar-months", startDate, endDate],
    queryFn: () =>
      apiGet<PeriodCalendarResponse>(
        `/api/v1/calendar/months${buildQuery({ start_date: startDate, end_date: endDate })}`,
      ),
  });
}

export function useFitness(startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["fitness", startDate, endDate],
    queryFn: () =>
      apiGet<FitnessDailyRollupOut[]>(
        `/api/v1/fitness${buildQuery({ start_date: startDate, end_date: endDate })}`,
      ),
  });
}

export function usePerformance(startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["performance", startDate, endDate],
    queryFn: () =>
      apiGet<PerformanceDailyRollupOut[]>(
        `/api/v1/performance${buildQuery({ start_date: startDate, end_date: endDate })}`,
      ),
  });
}

export function useHealthObservations(metricKeys: string[], startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["health-observations", metricKeys, startDate, endDate],
    queryFn: () => {
      const search = new URLSearchParams();
      for (const key of metricKeys) search.append("metric_key", key);
      search.set("start_date", startDate);
      search.set("end_date", endDate);
      search.set("limit", "500");
      return apiGet<Page<HealthObservationOut>>(`/api/v1/health/observations?${search.toString()}`);
    },
    enabled: metricKeys.length > 0,
  });
}

export function useHealthDashboard(startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["health-dashboard", startDate, endDate],
    queryFn: () =>
      apiGet<HealthDashboardOut>(
        `/api/v1/health/dashboard${buildQuery({ start_date: startDate, end_date: endDate })}`,
      ),
  });
}

/** One day's intraday health_stream series (currently only garmin.daily_body_battery.level) --
 * `enabled` should be gated the same way useActivityInsights/useActivityComparisons are, since
 * most days before this feature's own live-fetch start date have nothing to show. */
export function useHealthStream(metricKey: string, date: string, enabled: boolean) {
  return useQuery({
    queryKey: ["health-stream", metricKey, date],
    queryFn: () =>
      apiGet<HealthStreamResponse>(
        `/api/v1/health/stream${buildQuery({ metric_key: metricKey, date })}`,
      ),
    enabled,
  });
}

export function useActivities(filters: ActivityFilters) {
  return useQuery({
    queryKey: ["activities", filters],
    queryFn: () =>
      apiGet<Page<ActivitySummary>>(
        `/api/v1/activities${buildQuery({
          start_date: filters.startDate,
          end_date: filters.endDate,
          sport: filters.sport,
          limit: filters.limit,
          offset: filters.offset,
        })}`,
      ),
  });
}

/** Pages through every matching activity rather than capping at one request's 500-row limit --
 * an "all time" view can span a decade-plus of history, well past what's fine for a single
 * year/month view's one-shot fetch. Returns a flat array (not a Page<>), since every caller
 * just wants the full list to aggregate over client-side. */
/** Distinct years with at least one activity -- powers DateNavigator's year strip. Deliberately
 * NOT `useAllActivities({})`: that pages through every activity summary in the whole history
 * just to read `local_date`'s year off each one, which became the single slowest thing on every
 * calendar page (DateNavigator renders on all of them) once activity count reached the
 * thousands. `staleTime: Infinity` because this only changes when a brand-new year's first
 * activity is ingested -- not worth refetching on every navigation. */
export function useActivityYears() {
  return useQuery({
    queryKey: ["activity-years"],
    queryFn: () => apiGet<number[]>("/api/v1/activities/years"),
    staleTime: Infinity,
  });
}

export function useAllActivities(filters: Omit<ActivityFilters, "limit" | "offset">) {
  return useQuery({
    queryKey: ["all-activities", filters],
    queryFn: async () => {
      const pageSize = 500;
      const items: ActivitySummary[] = [];
      let offset = 0;
      for (;;) {
        const page = await apiGet<Page<ActivitySummary>>(
          `/api/v1/activities${buildQuery({
            start_date: filters.startDate,
            end_date: filters.endDate,
            sport: filters.sport,
            limit: pageSize,
            offset,
          })}`,
        );
        items.push(...page.items);
        if (page.items.length === 0 || items.length >= page.total) break;
        offset += pageSize;
      }
      return items;
    },
  });
}

export function useActivity(activityId: string) {
  return useQuery({
    queryKey: ["activity", activityId],
    queryFn: () => apiGet<ActivityDetail>(`/api/v1/activities/${activityId}`),
  });
}

/** Every GPS-bearing activity's start point (ADR 0011) -- a bounded, unpaginated response by
 * design (real scale: 904 of 1250 activities), so the map explorer fetches it in one shot. */
export function useActivityMapPoints(filters: ActivityFilters) {
  return useQuery({
    queryKey: ["activity-map-points", filters],
    queryFn: () =>
      apiGet<ActivityMapPointOut[]>(
        `/api/v1/activities/map${buildQuery({
          start_date: filters.startDate,
          end_date: filters.endDate,
          sport: filters.sport,
        })}`,
      ),
  });
}

/** Batch polyline lookup for thumbnail maps -- one request per rendered page of ActivityCards
 * (activity list / day view) rather than one request per card. `ids` is deliberately part of
 * the query key (not just enabled-gated) so switching pages/dates gets its own cache entry
 * rather than serving a stale page's routes. Disabled entirely for an empty id list -- there's
 * nothing to fetch, and an empty `ids=` string would still be a real (wasted) request. */
export function useActivityRoutes(ids: string[]) {
  return useQuery({
    queryKey: ["activity-routes", ids],
    queryFn: () => apiGet<ActivityRouteOut[]>(`/api/v1/activities/routes?ids=${ids.join(",")}`),
    enabled: ids.length > 0,
  });
}

/** `enabled` should be gated off for hiking (see ActivityDetailPage.tsx) -- "fastest for this
 * distance"/"recent efforts" reads as a running-style comparison, and only some hikes even have
 * enough same-sport peers to populate it, making its presence inconsistent from hike to hike. */
export function useActivityContext(activityId: string, enabled: boolean = true) {
  return useQuery({
    queryKey: ["activity-context", activityId],
    queryFn: () => apiGet<ActivityContextOut>(`/api/v1/activities/${activityId}/context`),
    enabled,
  });
}

/** The 10 most recent same-distance, same-start-location runs (GET /activities/{id}/comparisons)
 * -- `enabled` should be gated on the activity's sport the same way `useActivityInsights` below
 * already is, since this comparison is deliberately running-specific. */
export function useActivityComparisons(activityId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["activity-comparisons", activityId],
    queryFn: () =>
      apiGet<ActivityComparisonsOut>(`/api/v1/activities/${activityId}/comparisons`),
    enabled,
  });
}

/** The 10 most recent bouldering sessions of about the same total duration
 * (GET /activities/{id}/climb-comparisons) -- `enabled` should be gated on
 * `isBoulderingActivity`, same reasoning as `useActivityComparisons` above being running-only. */
export function useActivityClimbComparisons(activityId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["activity-climb-comparisons", activityId],
    queryFn: () =>
      apiGet<ClimbComparisonsOut>(`/api/v1/activities/${activityId}/climb-comparisons`),
    enabled,
  });
}

/** The weekly/monthly/yearly/all-time "Climbing" section's own aggregate
 * (GET /activities/climbing-summary) -- a real server-side aggregate, not a client-side
 * reduction over `ActivitySummary[]` the way `HikeStatsCard` works, since the per-grade
 * attempted/completed chart needs the full distribution across however many sessions fall in
 * the period, not just a few scalar totals. */
export function useClimbingSummary(startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["climbing-summary", startDate, endDate],
    queryFn: () =>
      apiGet<ClimbingSummaryOut>(
        `/api/v1/activities/climbing-summary${buildQuery({ start_date: startDate, end_date: endDate })}`,
      ),
  });
}

/** A manual "I logged the wrong status/grade" correction for one route (see
 * bouldering_overrides.py's own docstring) -- either or both may be sent in one call.
 * Invalidates the activity detail query so the corrected split flows straight back through the
 * same `splits` array everything else here already reads. */
export function useSetClimbRouteStatus(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      splitIndex,
      result,
      grade,
    }: {
      splitIndex: number;
      result?: string;
      grade?: number;
    }) =>
      apiPatch<SplitOut>(`/api/v1/activities/${activityId}/climb-routes/${splitIndex}`, {
        result,
        grade,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["activity", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activity-climb-comparisons", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["climbing-summary"] });
    },
  });
}

/** A route the device never tracked at all (see bouldering_overrides.py::add_manual_route's own
 * docstring) -- same invalidation set as useSetClimbRouteStatus, since an added route changes
 * the same route count/max grade/climb time everything else here reads. */
export function useAddClimbRoute(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { grade: number; result: string }) =>
      apiPost<SplitOut>(`/api/v1/activities/${activityId}/climb-routes`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["activity", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activities"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
      void queryClient.invalidateQueries({ queryKey: ["activity-climb-comparisons", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["climbing-summary"] });
    },
  });
}

/** Removes a manually-added route -- never a FIT-derived one (see
 * bouldering_overrides.py::delete_manual_route's own docstring). Same invalidation set as
 * useAddClimbRoute, its own inverse. */
export function useDeleteClimbRoute(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (splitIndex: number) =>
      apiDelete<void>(`/api/v1/activities/${activityId}/climb-routes/${splitIndex}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["activity", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activities"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
      void queryClient.invalidateQueries({ queryKey: ["activity-climb-comparisons", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["climbing-summary"] });
    },
  });
}

/** Commits a trim (see activity_trim.py's own docstring for what's recomputed vs. cleared) --
 * invalidates the activity detail (distance/duration/route/laps/calories all change), the
 * activity-list/calendar caches (their own totals for this activity's date are now stale until
 * the server's own rollup recompute lands and a refetch picks it up), and this activity's own
 * stream (now served windowed to the kept range, see stream_query.py's own `window` param). */
export function useTrimActivity(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { trim_start_s: number | null; trim_end_s: number | null }) =>
      apiPost<ActivityDetail>(`/api/v1/activities/${activityId}/trim`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["activity", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activity-stream", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activities"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
}

/** Undoes a trim, restoring the pristine pre-trim state -- same invalidation set as
 * useTrimActivity, its own inverse. */
export function useClearActivityTrim(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiDelete<ActivityDetail>(`/api/v1/activities/${activityId}/trim`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["activity", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activity-stream", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activities"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
}

/** Side-by-side field comparison for the merge UI (GET .../merge-preview/{otherId}) -- see
 * activity_merge.py's own docstring for exactly which fields are comparable. */
export function useActivityMergePreview(activityId: string, otherId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["activity-merge-preview", activityId, otherId],
    queryFn: () =>
      apiGet<ActivityMergePreviewOut>(
        `/api/v1/activities/${activityId}/merge-preview/${otherId}`,
      ),
    enabled,
  });
}

/** Merges another activity into this one (POST .../merge) -- this activity is always the
 * survivor. Same invalidation set as useTrimActivity: distance/duration/route/laps can all
 * change, and the absorbed activity disappearing changes the activity-list/calendar totals for
 * its date too. */
export function useMergeActivity(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { other_activity_id: string; field_choices: Record<string, string> }) =>
      apiPost<ActivityDetail>(`/api/v1/activities/${activityId}/merge`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["activity", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activity-stream", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activities"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
}

/** The Settings page's list-wide "needs trim" scan (GET /activities/needs-trim) -- surfaces
 * every hiking/walking activity transport_mix.py's own heuristic flags, so the athlete doesn't
 * have to stumble onto each one individually via its own detail page. Actually trimming still
 * happens on that activity's own detail page (ActivityTrimControls) -- this list only links
 * there, it doesn't duplicate the trim mutation. */
export function useTrimCandidates() {
  return useQuery({
    queryKey: ["trim-candidates"],
    queryFn: () => apiGet<TrimCandidateOut[]>("/api/v1/activities/needs-trim"),
  });
}

/** The Settings page's list-wide duplicate scan (GET /activities/possible-duplicates) -- same
 * "surface it here, act on its own detail page" split as useTrimCandidates above. See
 * activity_merge.py::find_all_duplicate_pairs for the detection/dedup logic. */
export function useDuplicatePairs() {
  return useQuery({
    queryKey: ["duplicate-pairs"],
    queryFn: () => apiGet<DuplicatePairOut[]>("/api/v1/activities/possible-duplicates"),
  });
}

/** Point-in-time insights for one activity (GET /activities/{id}/insights) -- always bounded to
 * that activity's own past, never anything after it. `enabled` should be gated on the activity's
 * sport (see ActivityDetailPage.tsx's `isRunningSport` check) rather than always-on like
 * `useActivityContext` above, since this panel is deliberately running-specific. */
export function useActivityInsights(activityId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["activity-insights", activityId],
    queryFn: () => apiGet<InsightOut[]>(`/api/v1/activities/${activityId}/insights`),
    enabled,
  });
}

/** `enabled` should be false when the activity has no GPS start point (checked from its own
 * `route` field, already in hand from `useActivity`) -- no point issuing a request the backend
 * will just answer `available: false` for. */
export function useActivityWeather(activityId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["activity-weather", activityId],
    queryFn: () => apiGet<ActivityWeatherOut>(`/api/v1/activities/${activityId}/weather`),
    enabled,
  });
}

/** City/town or national park name for the activity's GPS start point -- same gating rationale
 * as useActivityWeather above (only worth asking once route.start_lat is already known to
 * exist). On a cache miss the backend answers `available: false` immediately and kicks off the
 * real Nominatim lookup as a background task rather than blocking the response on it (the
 * project's own 1-req/s throttle for that vendor made the first-ever view of *any* activity
 * noticeably slow before this) -- so a `false` result here briefly polls to pick up the
 * now-cached value once that background fetch lands, capped at a few attempts rather than
 * forever, for the rare activity whose location genuinely never resolves. Once available, it's
 * cached indefinitely (staleTime: Infinity) -- a completed activity's location never changes. */
export function useActivityLocation(activityId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["activity-location", activityId],
    queryFn: () => apiGet<ActivityLocationOut>(`/api/v1/activities/${activityId}/location`),
    enabled,
    staleTime: Infinity,
    refetchInterval: (query) => {
      if (query.state.data?.available) return false;
      return query.state.dataUpdateCount < 5 ? 2000 : false;
    },
  });
}

/** The pre-planned workout structure (Garmin Connect's "Workout" builder) recorded into some
 * activities' own FIT files -- `null` for the (large majority of) activities with no such
 * plan. Always enabled: cheap (one row lookup), and unlike weather there's no cheaper
 * already-in-hand signal to gate it on. */
export function useActivityWorkout(activityId: string) {
  return useQuery({
    queryKey: ["activity-workout", activityId],
    queryFn: () => apiGet<ActivityWorkoutOut | null>(`/api/v1/activities/${activityId}/workout`),
  });
}

/** `tier` defaults to "low" (matches the Phase 4 MCP tool's own choice, ADR 0007 decision 7) --
 * fine for a compact single overview chart, but the Milestone C multi-panel activity detail
 * view asks for "medium" explicitly since several synced panels at once can use the extra
 * resolution. */
export function useActivityStream(activityId: string, enabled: boolean, tier: string = "low") {
  return useQuery({
    queryKey: ["activity-stream", activityId, tier],
    queryFn: () => apiGet<StreamResponse>(`/api/v1/activities/${activityId}/stream?tier=${tier}`),
    enabled,
  });
}

export function useActivitySources(activityId: string) {
  return useQuery({
    queryKey: ["activity-sources", activityId],
    queryFn: () => apiGet<ActivitySourcesOut>(`/api/v1/activities/${activityId}/sources`),
  });
}

/** Repoints one source's link onto a brand-new activity (see routers/activities.py's own
 * docstring) -- invalidates the sources panel for both the old and the newly-created activity,
 * plus the activity list/calendar, since a split changes how many activities exist. */
export function useSplitActivitySource(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (linkId: number) =>
      apiPost<ActivitySplitOut>(`/api/v1/activities/${activityId}/sources/${linkId}/split`, {}),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["activity-sources", activityId] });
      void queryClient.invalidateQueries({
        queryKey: ["activity-sources", result.new_activity_id],
      });
      void queryClient.invalidateQueries({ queryKey: ["activities"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
}

/** A manual "this sport is wrong" correction (see routers/activities.py's own docstring) --
 * invalidates every view that reads an activity's sport, since it can change which section
 * (running vs. hiking, etc.) an activity shows up under. */
export function useSetSportOverride(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { sport: string; sub_sport?: string | null }) =>
      apiPatch<ActivitySportOverrideOut>(`/api/v1/activities/${activityId}/sport`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["activity", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activities"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
      void queryClient.invalidateQueries({ queryKey: ["activity-context", activityId] });
    },
  });
}

/** A manual "this is/isn't a race" correction (see routers/activities.py's own docstring) --
 * for activities where garmin_activity_summary.py's eventTypeId heuristic misses a real race
 * the athlete never flagged as one inside the Garmin Connect app itself. */
export function useSetRaceOverride(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { is_race: boolean }) =>
      apiPatch<ActivityRaceOverrideOut>(`/api/v1/activities/${activityId}/race`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["activity", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activities"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
}

/** A manual "this title is wrong" correction (see routers/activities.py's own docstring) --
 * Garmin Connect's own name isn't reliably a real athlete-given title (it can be just as
 * generic a template as the FIT-derived default), so there's no automatic fix for this. */
export function useSetNameOverride(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string }) =>
      apiPatch<ActivityNameOverrideOut>(`/api/v1/activities/${activityId}/name`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["activity", activityId] });
      void queryClient.invalidateQueries({ queryKey: ["activities"] });
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
}

/** The athlete's own carbohydrate/sodium intake for this activity -- unlike the sport/race/name
 * corrections above, there's no vendor source to correct here (see sport_override.py's own
 * docstring); this is the athlete's only input. */
export function useSetFuelingOverride(activityId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { carbohydrates_g?: number | null; sodium_mg?: number | null }) =>
      apiPatch<ActivityFuelingOut>(`/api/v1/activities/${activityId}/fueling`, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["activity", activityId] });
    },
  });
}

export function useInsights() {
  return useQuery({
    queryKey: ["insights"],
    queryFn: () => apiGet<InsightOut[]>("/api/v1/insights"),
  });
}

/** Total time-in-pace-band across the athlete's whole running history -- a precomputed,
 * per-second-accurate aggregate (see pace_bands.py), not something derived client-side from
 * each run's own average pace. */
export function usePaceBands() {
  return useQuery({
    queryKey: ["insights", "pace-bands"],
    queryFn: () => apiGet<PaceBandOut[]>("/api/v1/insights/pace-bands"),
  });
}

/** Per-activity time-in-band breakdown -- the composition-over-time strip above the aggregate
 * bar chart usePaceBands() feeds. */
export function usePaceBandsByActivity() {
  return useQuery({
    queryKey: ["insights", "pace-bands", "by-activity"],
    queryFn: () => apiGet<ActivityPaceBandsOut[]>("/api/v1/insights/pace-bands/by-activity"),
  });
}

export function useSleep(startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["sleep", startDate, endDate],
    queryFn: () =>
      apiGet<SleepSessionOut[]>(
        `/api/v1/sleep${buildQuery({ start_date: startDate, end_date: endDate })}`,
      ),
  });
}

export function useNotes(entityType: string, entityId: string) {
  return useQuery({
    queryKey: ["notes", entityType, entityId],
    queryFn: () =>
      apiGet<NoteOut[]>(
        `/api/v1/notes${buildQuery({ entity_type: entityType, entity_id: entityId })}`,
      ),
  });
}

export function useCreateNote() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: NoteCreate) => apiPost<NoteOut>("/api/v1/notes", payload),
    onSuccess: (note) => {
      void queryClient.invalidateQueries({
        queryKey: ["notes", note.entity_type, note.entity_id],
      });
      void queryClient.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
}

/** An athlete's own configured HR training zones (see hr_zones.py's own docstring for the
 * blended-formula rationale) -- independent of any single activity, so this isn't scoped to an
 * activity id the way the sport/race/name corrections above are. */
export function useHrZoneConfig() {
  return useQuery({
    queryKey: ["hr-zone-config"],
    queryFn: () => apiGet<HrZoneConfigOut>("/api/v1/settings/hr-zones"),
  });
}

export function useSetHrZoneConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: HrZoneConfigIn) => apiPut<HrZoneConfigOut>("/api/v1/settings/hr-zones", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["hr-zone-config"] });
    },
  });
}

/** An athlete's own configured running threshold pace (see running_load.py's own docstring) --
 * the calibration constant behind the running-specific rTSS that Fitness & Form's CTL/ATL now
 * prefers over Garmin's own training_load_peak. */
export function useRunningLoadConfig() {
  return useQuery({
    queryKey: ["running-load-config"],
    queryFn: () => apiGet<RunningLoadConfigOut>("/api/v1/settings/running-load"),
  });
}

export function useSetRunningLoadConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RunningLoadConfigIn) =>
      apiPut<RunningLoadConfigOut>("/api/v1/settings/running-load", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["running-load-config"] });
      // The PUT itself recomputes fitness_daily_rollup/insights server-side immediately (see
      // api/routers/settings.py) -- invalidate here so any Fitness & Form view already open
      // picks up the new CTL/ATL/TSB without a manual refresh.
      void queryClient.invalidateQueries({ queryKey: ["fitness"] });
      void queryClient.invalidateQueries({ queryKey: ["insights"] });
    },
  });
}

/** An athlete's optional profile facts (birthdate/height/sex) -- used only as inputs to
 * formula-based fallbacks elsewhere (max HR, BMR) when there isn't enough empirical/device data
 * yet. See api/schemas/settings.py::AthleteProfileIn. */
export function useAthleteProfile() {
  return useQuery({
    queryKey: ["athlete-profile"],
    queryFn: () => apiGet<AthleteProfileOut>("/api/v1/settings/profile"),
  });
}

export function useSetAthleteProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AthleteProfileIn) =>
      apiPut<AthleteProfileOut>("/api/v1/settings/profile", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["athlete-profile"] });
      // Both fallbacks are computed at read time (BMR) or on next ingest (max HR) from this
      // profile -- invalidate so an already-open Insights/Health view picks up the change without
      // a manual refresh (BMR immediately; max HR after the next sync recomputes
      // performance_daily_rollup).
      void queryClient.invalidateQueries({ queryKey: ["performance"] });
      void queryClient.invalidateQueries({ queryKey: ["health-dashboard"] });
    },
  });
}

/** Self-service password change -- verifies the current password server-side first. */
export function useChangePassword() {
  return useMutation({
    mutationFn: (body: ChangePasswordIn) =>
      apiPut<ChangePasswordOut>("/api/v1/settings/password", body),
  });
}

/** Whether the athlete has a Eufy scale account connected (see adapters/eufy.py's own module
 * docstring) -- never carries the password, only the configured email so the athlete can
 * confirm which account is linked. */
export function useEufyStatus() {
  return useQuery({
    queryKey: ["eufy-status"],
    queryFn: () => apiGet<EufyStatusOut>("/api/v1/settings/eufy/status"),
  });
}

/** Verifies the credential against Eufy's own login endpoint before saving it -- see
 * settings.py::post_eufy_login's own docstring. */
export function useEufyLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: EufyLoginIn) => apiPost<EufyLoginOut>("/api/v1/settings/eufy/login", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["eufy-status"] });
    },
  });
}

/** Whether the athlete has published a Google-Calendar-subscribable feed of their planned_workout
 * calendar (see calendar_feed.py's own module docstring), and when. Never carries the feed URL
 * itself -- that's returned once by usePublishCalendarFeed, on create/rotate. */
export function useCalendarFeedStatus() {
  return useQuery({
    queryKey: ["calendar-feed-status"],
    queryFn: () => apiGet<CalendarFeedStatusOut>("/api/v1/settings/calendar-feed"),
  });
}

/** Always mints a fresh token, whether this is the first publish or a rotation -- the only
 * operation that ever makes sense here (see settings.py's own POST handler docstring). */
export function usePublishCalendarFeed() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost<CalendarFeedUrlOut>("/api/v1/settings/calendar-feed", {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["calendar-feed-status"] });
    },
  });
}

export function useUnpublishCalendarFeed() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiDelete<CalendarFeedStatusOut>("/api/v1/settings/calendar-feed"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["calendar-feed-status"] });
    },
  });
}

/** Token store presence/age + the most recent garmin_connect sync result -- read-only, no
 * network call to Garmin itself (see token_store_status's own docstring). */
export function useGarminStatus() {
  return useQuery({
    queryKey: ["garmin-status"],
    queryFn: () => apiGet<GarminAuthStatusOut>("/api/v1/settings/garmin/status"),
  });
}

/** A human-initiated, one-shot Garmin login (see login_with_credentials's own docstring) --
 * does not support Garmin's MFA challenge; a 422 response means the account needs `sync auth
 * login` from a terminal instead. */
export function useGarminLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: GarminLoginIn) =>
      apiPost<GarminLoginOut>("/api/v1/settings/garmin/login", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["garmin-status"] });
    },
  });
}

/** Triggers the same sync_garmin_connect() call the daily worker already makes, once, right
 * now. Runs in the background on the server -- see useLatestJob below for progress. */
export function useTriggerGarminSync() {
  return useMutation({
    mutationFn: () => apiPost<JobTriggerOut>("/api/v1/settings/garmin/sync", {}),
  });
}

/** Triggers `sync rebuild`'s web counterpart -- never destructive (raw-first: only derived
 * tables are wiped and replayed from the archive). See useLatestJob below for progress. */
export function useTriggerRebuild() {
  return useMutation({
    mutationFn: () => apiPost<JobTriggerOut>("/api/v1/settings/rebuild", {}),
  });
}

/** Uploads a Garmin or Strava bulk-export .zip for import -- the web counterpart of `sync
 * import garmin-export`/`sync import strava-export <path>`. Runs in the background on the
 * server -- see useLatestJob below for progress. */
export function useUploadBulkExport() {
  return useMutation({
    mutationFn: ({ kind, file }: { kind: "garmin" | "strava"; file: File }) => {
      const formData = new FormData();
      formData.append("kind", kind);
      formData.append("file", file);
      return apiPostForm<JobTriggerOut>("/api/v1/settings/import/bulk-export", formData);
    },
  });
}

/** Status polling for the three triggers above, all backed by the same `ingest_run` row shape
 * -- same "poll while running, stop once settled" pattern as useActivityLocation above (the
 * only other polling precedent in this app), just keyed by job source instead of activity id.
 * `enabled` lets a card only start polling once its own trigger has actually fired. */
export function useLatestJob(source: JobSource, enabled: boolean) {
  return useQuery({
    queryKey: ["latest-job", source],
    queryFn: () =>
      apiGet<JobStatusOut | null>(`/api/v1/settings/jobs/latest?source=${source}`),
    enabled,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 2000 : false),
  });
}

/** A distance goal for a whole calendar year or month, plus its progress line -- `periodStart`
 * is "YYYY" for period_type "year", "YYYY-MM" for "month" (see goals.py::period_bounds).
 * `available: false` in the response means no goal is set for this period yet, not an error. */
export function useGoalProgress(periodType: "year" | "month", periodStart: string) {
  return useQuery({
    queryKey: ["goal-progress", periodType, periodStart],
    queryFn: () =>
      apiGet<GoalProgressOut>(
        `/api/v1/goals${buildQuery({ period_type: periodType, period_start: periodStart })}`,
      ),
  });
}

export interface SetGoalInput {
  period_type: "year" | "month";
  period_start: string;
  sport: string | null;
  target_distance_m: number;
}

export function useSetGoal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SetGoalInput) => apiPut<GoalOut>("/api/v1/goals", body),
    onSuccess: (_goal, variables) => {
      void queryClient.invalidateQueries({
        queryKey: ["goal-progress", variables.period_type, variables.period_start],
      });
    },
  });
}

export function useDeleteGoal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (goal: GoalOut) => apiDelete<void>(`/api/v1/goals/${goal.id}`),
    onSuccess: (_void, goal) => {
      void queryClient.invalidateQueries({
        queryKey: ["goal-progress", goal.period_type, goal.period_start],
      });
    },
  });
}

// --- Scheduled workouts (planned_workout) -- see api/routers/planned_workouts.py and
// docs/adr/0015-scheduled-workouts.md. `usePlannedWorkoutsList` backs the calendar grid's own
// per-day indicator; `usePlannedWorkout` backs the day panel/schedule form once a day is
// expanded.

export function usePlannedWorkoutsList(startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["planned-workouts", startDate, endDate],
    queryFn: () =>
      apiGet<PlannedWorkoutListItemOut[]>(
        `/api/v1/planned-workouts${buildQuery({ start_date: startDate, end_date: endDate })}`,
      ),
  });
}

export function usePlannedWorkout(localDate: string) {
  return useQuery({
    queryKey: ["planned-workout", localDate],
    queryFn: () => apiGet<PlannedWorkoutOut>(`/api/v1/planned-workouts/${localDate}`),
  });
}

export interface SavePlannedWorkoutInput {
  localDate: string;
  sport: string;
  name: string | null;
  source_text: string | null;
  scheduled_time?: string | null;
  duration_minutes?: number | null;
  // hiit/strength_training only, see PlannedWorkoutStepIn
  steps?: PlannedWorkoutStepIn[] | null;
}

export function useSavePlannedWorkout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SavePlannedWorkoutInput) =>
      apiPut<PlannedWorkoutOut>(`/api/v1/planned-workouts/${body.localDate}`, {
        sport: body.sport,
        name: body.name,
        source_text: body.source_text,
        scheduled_time: body.scheduled_time ?? null,
        duration_minutes: body.duration_minutes ?? null,
        steps: body.steps ?? null,
      }),
    onSuccess: (_workout, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["planned-workout", variables.localDate] });
      void queryClient.invalidateQueries({ queryKey: ["planned-workouts"] });
    },
  });
}

export function useDeletePlannedWorkout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (localDate: string) => apiDelete<void>(`/api/v1/planned-workouts/${localDate}`),
    onSuccess: (_void, localDate) => {
      void queryClient.invalidateQueries({ queryKey: ["planned-workout", localDate] });
      void queryClient.invalidateQueries({ queryKey: ["planned-workouts"] });
    },
  });
}

export function usePushPlannedWorkout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (localDate: string) =>
      apiPost<JobTriggerOut>(`/api/v1/planned-workouts/${localDate}/push`, {}),
    onSuccess: (_result, localDate) => {
      void queryClient.invalidateQueries({ queryKey: ["planned-workout", localDate] });
    },
  });
}

export interface RecurringPlannedWorkoutInput {
  local_date: string;
  sport: string;
  name: string | null;
  source_text: string | null;
  scheduled_time?: string | null;
  duration_minutes?: number | null;
  // hiit/strength_training only, see PlannedWorkoutStepIn
  steps?: PlannedWorkoutStepIn[] | null;
  frequency: "weekly" | "every_n_days" | "monthly";
  interval_days?: number;
  count?: number;
  until?: string;
}

export function useCreateRecurringPlannedWorkouts() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RecurringPlannedWorkoutInput) =>
      apiPost<RecurringWorkoutOut>("/api/v1/planned-workouts/recurring", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["planned-workouts"] });
      void queryClient.invalidateQueries({ queryKey: ["planned-workout"] });
    },
  });
}

/** Creates an unauthenticated share link for one activity -- see sharing.py's own docstring
 * for what's deliberately excluded from the public page (weight, HR). */
export function useCreateActivityShare(activityId: string) {
  return useMutation({
    mutationFn: () => apiPost<ShareLinkOut>(`/api/v1/activities/${activityId}/share`, {}),
  });
}

/** Same as above, for a summary period -- "all" needs no periodStart. */
export function useCreatePeriodShare(
  periodType: "week" | "month" | "year" | "all",
  periodStart?: string,
) {
  return useMutation({
    mutationFn: () => {
      const qs = periodStart ? `?period_start=${encodeURIComponent(periodStart)}` : "";
      return apiPost<ShareLinkOut>(`/api/v1/periods/${periodType}/share${qs}`, {});
    },
  });
}

export function useRevokeShare() {
  return useMutation({
    mutationFn: (id: number) => apiPost<RevokeShareOut>(`/api/v1/share/${id}/revoke`, {}),
  });
}
