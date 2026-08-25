// Settings page: the web counterpart of `sync rebuild`. Never destructive -- only derived
// tables are wiped and replayed from the raw archive (raw-first: nothing here can lose data).
import { useState } from "react";

import { useLatestJob, useTriggerRebuild } from "../api/queries";
import { JobStatusBanner } from "./JobStatusBanner";

export function RebuildCard() {
  const rebuild = useTriggerRebuild();
  const [triggered, setTriggered] = useState(false);
  const job = useLatestJob("rebuild", triggered);
  const running = rebuild.isPending || job.data?.status === "running";

  return (
    <section className="card">
      <h2>Rebuild from archive</h2>
      <p className="chart-note">
        Replays every FIT/JSON/GPX/TCX file ever ingested and recomputes everything derived from
        it. Never destructive — raw data is never touched, only rebuilt from it — but can take
        several minutes on a large archive.
      </p>
      <button
        className="button"
        type="button"
        onClick={() => rebuild.mutate(undefined, { onSuccess: () => setTriggered(true) })}
        disabled={running}
      >
        {running ? "Rebuilding…" : "Rebuild now"}
      </button>
      <JobStatusBanner job={job.data} isPolling={triggered} />
    </section>
  );
}
