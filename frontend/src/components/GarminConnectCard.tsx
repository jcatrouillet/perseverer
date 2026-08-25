// Settings page: Garmin Connect status, a one-shot login form, and a "sync now" trigger. See
// login_with_credentials's own docstring (adapters/garmin_connect.py) for why the login form is
// safe (only the resulting session is persisted, the password never is) and why it deliberately
// can't complete Garmin's MFA challenge -- that's the one case this form can't handle, and it
// says so plainly rather than pretending to.
import { useState } from "react";

import {
  useGarminLogin,
  useGarminStatus,
  useLatestJob,
  useTriggerGarminSync,
} from "../api/queries";
import { JobStatusBanner } from "./JobStatusBanner";

function formatAge(days: number | null): string {
  if (days == null) return "";
  if (days === 0) return "today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

function loginErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  // Not 401 -- deliberately, see the endpoint's own docstring (api/routers/settings.py):
  // this app's client treats any 401 as "your own session expired" and force-logs-out.
  if (message.includes("(400)")) return "Incorrect Garmin username or password.";
  if (message.includes("(422)")) {
    return "This account requires an MFA code, which this form can't complete -- run `sync auth login` in a terminal instead.";
  }
  if (message.includes("(429)")) {
    return "Garmin rate-limited this attempt -- wait before retrying.";
  }
  return "Could not sign in.";
}

export function GarminConnectCard() {
  const status = useGarminStatus();
  const login = useGarminLogin();
  const sync = useTriggerGarminSync();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [syncTriggered, setSyncTriggered] = useState(false);
  const syncJob = useLatestJob("garmin_connect", syncTriggered);
  const syncRunning = sync.isPending || syncJob.data?.status === "running";

  function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    login.mutate(
      { username, password },
      { onSettled: () => setPassword("") }, // never lingers in state, success or failure
    );
  }

  function handleSync() {
    sync.mutate(undefined, { onSuccess: () => setSyncTriggered(true) });
  }

  return (
    <section className="card">
      <h2>Garmin Connect</h2>
      <p className="chart-note">
        A daily sync already runs automatically once connected. Log in here to (re)connect, or
        trigger a sync right now instead of waiting for the next scheduled run.
      </p>

      {status.data && (
        <ul className="settings-garmin__status">
          <li>
            {status.data.token_store_present
              ? `Connected — session established ${formatAge(status.data.token_store_age_days)}.`
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
          {status.data.staleness_severity && (
            <li
              role="alert"
              className={
                status.data.staleness_severity === "critical"
                  ? "settings-garmin__staleness settings-garmin__staleness--critical"
                  : "settings-garmin__staleness settings-garmin__staleness--warning"
              }
            >
              ⚠ {status.data.staleness_message}
            </li>
          )}
        </ul>
      )}

      <form className="settings-garmin__login-form" onSubmit={handleLogin}>
        <label className="field">
          Garmin username
          <input
            className="input"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
            required
          />
        </label>
        <label className="field">
          Garmin password
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

      <button className="button" type="button" onClick={handleSync} disabled={syncRunning}>
        {syncRunning ? "Syncing…" : "Sync now"}
      </button>
      <JobStatusBanner job={syncJob.data} isPolling={syncTriggered} />
    </section>
  );
}
