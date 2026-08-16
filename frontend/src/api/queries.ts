// One useQuery/useMutation hook per REST endpoint (src/sporthealth/api/routers/*.py). Note
// mutations invalidate both the calendar and the activity/day's own note list, since a note
// can be created from either the calendar page or the activity detail page.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPatch, apiPost, apiPut } from "./client";
import type {
  ActivityContextOut,
  ActivityDetail,
  ActivityMapPointOut,
  ActivityNameOverrideOut,
  ActivityRouteOut,
  ActivitySourcesOut,
  ActivitySplitOut,
  ActivityRaceOverrideOut,
  ActivitySportOverrideOut,
  ActivitySummary,
  ActivityWeatherOut,
  ActivityWorkoutOut,
  CalendarResponse,
  FitnessDailyRollupOut,
  HealthDashboardOut,
  HealthObservationOut,
  HrZoneConfigIn,
  HrZoneConfigOut,
  InsightOut,
  NoteCreate,
  NoteOut,
  Page,
  PeriodCalendarResponse,
  SleepSessionOut,
  StreamResponse,
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

export function useActivityContext(activityId: string) {
  return useQuery({
    queryKey: ["activity-context", activityId],
    queryFn: () => apiGet<ActivityContextOut>(`/api/v1/activities/${activityId}/context`),
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

export function useInsights() {
  return useQuery({
    queryKey: ["insights"],
    queryFn: () => apiGet<InsightOut[]>("/api/v1/insights"),
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
