// Shared "is this triggered background job done yet" display for GarminConnectCard/RebuildCard/
// BulkImportCard -- all three poll the same GET /settings/jobs/latest shape (useLatestJob), so
// this renders its three possible states consistently instead of each card hand-rolling its own
// running/success/failed copy.
import type { JobStatusOut } from "../api/types";

export function JobStatusBanner({
  job,
  isPolling,
}: {
  job: JobStatusOut | null | undefined;
  /** True once a trigger has actually fired this session -- avoids showing a stale job's
   * result (e.g. yesterday's successful sync) as if it just happened before the user has
   * clicked anything. */
  isPolling: boolean;
}) {
  if (!isPolling || !job) return null;

  if (job.status === "running") {
    return <p className="settings-job settings-job--running">Running…</p>;
  }
  if (job.status === "failed") {
    return (
      <p className="settings-job settings-job--failed" role="alert">
        Failed{job.first_error ? `: ${job.first_error}` : "."}
      </p>
    );
  }
  // items_new === items_seen for a rebuild (every replayed object counts as both) -- the
  // simpler phrasing avoids reading like a redundant "3 new, 3 seen" for that case.
  return (
    <p className="settings-job settings-job--success">
      {job.items_new === job.items_seen
        ? `Done -- ${job.items_seen} processed.`
        : `Done -- ${job.items_new} new, ${job.items_seen} seen.`}
    </p>
  );
}
