// One useQuery/useMutation hook per REST endpoint (src/sporthealth/api/routers/*.py). Note
// mutations invalidate both the calendar and the activity/day's own note list, since a note
// can be created from either the calendar page or the activity detail page.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "./client";
import type {
  ActivityDetail,
  ActivitySummary,
  CalendarResponse,
  NoteCreate,
  NoteOut,
  Page,
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

export function useActivity(activityId: string) {
  return useQuery({
    queryKey: ["activity", activityId],
    queryFn: () => apiGet<ActivityDetail>(`/api/v1/activities/${activityId}`),
  });
}

export function useActivityStream(activityId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["activity-stream", activityId],
    // Always the low tier -- matches the Phase 4 MCP tool's own choice (ADR 0007 decision 7):
    // this is a dashboard overview chart, not a full-resolution analysis view.
    queryFn: () => apiGet<StreamResponse>(`/api/v1/activities/${activityId}/stream?tier=low`),
    enabled,
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
