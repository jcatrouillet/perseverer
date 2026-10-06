// Settings page: web counterpart of `sync import garmin-export`/`sync import strava-export
// <path>` -- uploads a bulk-export .zip instead of pointing at a path already on disk.
import { useState } from "react";

import { useLatestJob, useUploadBulkExport } from "../api/queries";
import { JobStatusBanner } from "./JobStatusBanner";

type Kind = "garmin" | "strava";

export function BulkImportCard() {
  const upload = useUploadBulkExport();
  const [kind, setKind] = useState<Kind>("garmin");
  const [file, setFile] = useState<File | null>(null);
  const [triggeredKind, setTriggeredKind] = useState<Kind | null>(null);
  const job = useLatestJob(
    triggeredKind === "garmin" ? "garmin_export" : "strava_export",
    triggeredKind != null,
  );
  const running = upload.isPending || job.data?.status === "running";

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    upload.mutate(
      { kind, file },
      {
        onSuccess: () => {
          setTriggeredKind(kind);
          setFile(null);
        },
      },
    );
  }

  return (
    <section className="card">
      <h2>Bulk import</h2>
      <p className="chart-note">
        Import a full "export your data" archive from Garmin or Strava. Always safe to re-run —
        already-imported activities are recognized and skipped, not duplicated. Very large exports
        may be rejected by a reverse proxy in front of this app before reaching it.
      </p>
      <form className="settings-bulk-import__form" onSubmit={handleSubmit}>
        <label className="field">
          Source
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
            <option value="garmin">Garmin export</option>
            <option value="strava">Strava export</option>
          </select>
        </label>
        <label className="field">
          Export file (.zip)
          <input
            className="input"
            type="file"
            accept=".zip"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            required
          />
        </label>
        <button className="button button--primary" type="submit" disabled={running || !file}>
          {running ? "Uploading…" : "Upload and import"}
        </button>
        {upload.isError && (
          <span role="alert" className="settings-garmin__error">
            Could not upload -- make sure it's a .zip export archive.
          </span>
        )}
      </form>
      <JobStatusBanner job={job.data} isPolling={triggeredKind != null} />
    </section>
  );
}
