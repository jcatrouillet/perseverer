// Wraps the whole app. Renders children once a credential is stored; otherwise a two-tab
// form -- password login (issues a JWT) or a pasted per-athlete API key (see
// sync athlete set-password / create-key). Re-shows itself if a background request clears the
// stored credential (AUTH_CLEARED_EVENT), e.g. an expired JWT. See ADR 0008.
import { useEffect, useState } from "react";

import {
  AUTH_CLEARED_EVENT,
  AuthError,
  hasStoredCredential,
  login,
  ServerUnconfiguredError,
  storeApiKey,
  storeJwt,
} from "../api/client";
import "../styles/auth.css";
import { ThemeToggle } from "./ThemeToggle";

type Tab = "login" | "api-key";

function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const { access_token } = await login({ username, password });
      storeJwt(access_token);
      onSuccess();
    } catch (err) {
      if (err instanceof AuthError) {
        setError("Invalid username or password.");
      } else if (err instanceof ServerUnconfiguredError) {
        setError("Server is not configured for password login (SPORTHEALTH_JWT_SECRET unset).");
      } else {
        setError("Login failed. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="auth-form" onSubmit={(e) => void handleSubmit(e)}>
      <label className="field">
        Username
        <input
          className="input"
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          required
        />
      </label>
      <label className="field">
        Password
        <input
          className="input"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
      </label>
      {error && (
        <p className="auth-form__error" role="alert">
          {error}
        </p>
      )}
      <button className="button button--primary button--full" type="submit" disabled={submitting}>
        {submitting ? "Signing in…" : "Log in"}
      </button>
    </form>
  );
}

function ApiKeyForm({ onSuccess }: { onSuccess: () => void }) {
  const [apiKey, setApiKey] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!apiKey.trim()) return;
    // No validation round-trip here by design (see ADR 0008) -- the next real API call
    // proves whether the key is valid; an invalid one surfaces as the usual 401 re-prompt.
    storeApiKey(apiKey.trim());
    onSuccess();
  }

  return (
    <form className="auth-form" onSubmit={handleSubmit}>
      <label className="field">
        API key
        <input
          className="input"
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="generated via `sync athlete create-key`"
          required
        />
      </label>
      <button className="button button--primary button--full" type="submit">
        Use this key
      </button>
    </form>
  );
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [authenticated, setAuthenticated] = useState(hasStoredCredential);
  const [tab, setTab] = useState<Tab>("login");

  useEffect(() => {
    function onAuthCleared() {
      setAuthenticated(false);
    }
    window.addEventListener(AUTH_CLEARED_EVENT, onAuthCleared);
    return () => window.removeEventListener(AUTH_CLEARED_EVENT, onAuthCleared);
  }, []);

  if (authenticated) {
    return <>{children}</>;
  }

  return (
    <div className="auth-page">
      <div className="auth-page__toggle">
        <ThemeToggle />
      </div>
      <main className="auth-card">
        <div className="auth-card__brand">
          <div className="auth-card__logo">SH</div>
          <h1>Sport Health</h1>
          <p className="auth-card__subtitle">Sign in to view your training and health data</p>
        </div>
        <div className="auth-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "login"}
            className={tab === "login" ? "is-active" : ""}
            onClick={() => setTab("login")}
            disabled={tab === "login"}
          >
            Log in
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "api-key"}
            className={tab === "api-key" ? "is-active" : ""}
            onClick={() => setTab("api-key")}
            disabled={tab === "api-key"}
          >
            API key
          </button>
        </div>
        {tab === "login" ? (
          <LoginForm onSuccess={() => setAuthenticated(true)} />
        ) : (
          <ApiKeyForm onSuccess={() => setAuthenticated(true)} />
        )}
      </main>
    </div>
  );
}
