// Settings page: Eufy scale status and a credential-entry form. See adapters/eufy.py's own
// module docstring for why this is a plain login form (unlike Garmin, Eufy credentials are
// persisted in full so the daily sync can use them) -- POST /settings/eufy/login verifies the
// credential against Eufy's own login endpoint before saving it.
import { useState } from "react";

import { useEufyLogin, useEufyStatus } from "../api/queries";

function loginErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  // Not 401 -- deliberately, same reasoning as GarminConnectCard.tsx: this app's client treats
  // any 401 as "your own session expired" and force-logs-out.
  if (message.includes("(400)")) return "Incorrect Eufy email or password.";
  if (message.includes("(502)")) return "Could not reach Eufy -- try again.";
  return "Could not save.";
}

export function EufyCard() {
  const status = useEufyStatus();
  const login = useEufyLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [customerId, setCustomerId] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    login.mutate(
      { email, password, device_id: deviceId, customer_id: customerId },
      { onSettled: () => setPassword("") }, // never lingers in state, success or failure
    );
  }

  return (
    <section className="card">
      <h2>Eufy scale</h2>
      <p className="chart-note">
        Body composition (weight, body fat, muscle mass, and more) from a Eufy smart scale. A
        daily sync already runs automatically once connected.
      </p>

      {status.data && (
        <ul className="settings-garmin__status">
          <li>
            {status.data.configured
              ? `Connected — ${status.data.email}.`
              : "Not connected yet."}
          </li>
        </ul>
      )}

      <form className="settings-garmin__login-form" onSubmit={handleSubmit}>
        <label className="field">
          Eufy account email
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
          Eufy account password
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="off"
            required
          />
        </label>
        <label className="field">
          Device ID
          <input
            className="input"
            type="text"
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
            autoComplete="off"
            required
          />
        </label>
        <label className="field">
          Customer ID
          <input
            className="input"
            type="text"
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            autoComplete="off"
            required
          />
        </label>
        <p className="chart-note">
          Verified against Eufy before saving. Stored so future syncs can use it (unlike Garmin's
          own login above, this is not a one-time-use credential).
        </p>
        <button className="button button--primary" type="submit" disabled={login.isPending}>
          {login.isPending ? "Saving…" : "Save"}
        </button>
        {login.isError && (
          <span role="alert" className="settings-garmin__error">
            {loginErrorMessage(login.error)}
          </span>
        )}
        {login.isSuccess && <span className="settings-garmin__saved">Saved.</span>}
      </form>
    </section>
  );
}
