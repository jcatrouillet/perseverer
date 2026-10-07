// Settings page: Kaya (the bouldering logbook app) status, a one-shot login form, and a "sync now"
// trigger -- the web counterpart of `sync auth kaya-login` / `sync import kaya`
// (docs/ARCHITECTURE.md). Only the resulting session is saved, never the
// password. A daily sync already runs automatically once connected.
import { useEffect, useState } from "react";

import { useKayaLogin, useKayaStatus, useLatestJob, useTriggerKayaSync } from "../api/queries";
import { JobStatusBanner } from "./JobStatusBanner";

function formatAge(days: number | null): string {
  if (days == null) return "";
  if (days === 0) return "today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

function loginErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  // 400, not 401 -- see the endpoint's own docstring (api/routers/settings.py): this app's client
  // treats any 401 as "your own session expired" and force-logs-out.
  if (message.includes("(400)")) return "Incorrect Kaya email or password.";
  if (message.includes("(502)")) return "Could not reach Kaya — try again.";
  return "Could not sign in.";
}

export function KayaCard() {
  const status = useKayaStatus();
  const login = useKayaLogin();
  const sync = useTriggerKayaSync();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [syncTriggered, setSyncTriggered] = useState(false);
  const syncJob = useLatestJob("kaya", syncTriggered);
  const syncRunning = sync.isPending || syncJob.data?.status === "running";

  // The status line ("Last sync succeeded") comes from the same ingest_run row the job poll reads,
  // but is a separate query -- refresh it the moment the job settles.
  const jobStatus = syncJob.data?.status;
  const refetchStatus = status.refetch;
  useEffect(() => {
    if (jobStatus === "success" || jobStatus === "failed") void refetchStatus?.();
  }, [jobStatus, refetchStatus]);

  function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    login.mutate(
      { email, password },
      { onSettled: () => setPassword("") }, // never lingers in state, success or failure
    );
  }

  function handleSync() {
    sync.mutate(undefined, { onSuccess: () => setSyncTriggered(true) });
  }

  return (
    <section className="card">
      <h2>Kaya</h2>
      <p className="chart-note">
        Your bouldering logbook from the Kaya app — routes, attempts and sends — merged with your
        Garmin bouldering sessions. A daily sync already runs automatically once connected. Log in
        here to (re)connect, or sync right now.
      </p>

      {status.data && (
        <ul className="settings-garmin__status">
          <li>
            {status.data.session_present
              ? `Connected — signed in ${formatAge(status.data.session_age_days)}.`
              : "Not connected yet."}
          </li>
          <li>
            {status.data.last_sync_status == null
              ? "No sync has run yet."
              : status.data.last_sync_status === "success"
                ? "Last sync succeeded."
                : status.data.last_sync_status === "failed"
                  ? `Last sync failed${status.data.last_sync_error ? `: ${status.data.last_sync_error}` : "."}`
                  : "A sync is currently running."}
          </li>
        </ul>
      )}

      <form className="settings-garmin__login-form" onSubmit={handleLogin}>
        <label className="field">
          Kaya email
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="off"
            required
          />
        </label>
        <label className="field">
          Kaya password
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="off"
            required
          />
        </label>
        <p className="chart-note">
          Used once to sign in and never stored — only the resulting session is saved.
        </p>
        <button className="button button--primary" type="submit" disabled={login.isPending}>
          {login.isPending ? "Signing in…" : "Log in"}
        </button>
        {login.isError && (
          <span role="alert" className="settings-garmin__error">
            {loginErrorMessage(login.error)}
          </span>
        )}
        {login.isSuccess && <span className="settings-garmin__saved">Signed in.</span>}
      </form>

      <button
        className="button"
        type="button"
        onClick={handleSync}
        disabled={syncRunning || !status.data?.session_present}
        title={status.data?.session_present ? undefined : "Log in to Kaya first"}
      >
        {syncRunning ? "Syncing…" : "Sync now"}
      </button>
      {sync.isError && (
        <span role="alert" className="settings-garmin__error">
          Could not start the sync.
        </span>
      )}
      <JobStatusBanner job={syncJob.data} isPolling={syncTriggered} />
    </section>
  );
}
