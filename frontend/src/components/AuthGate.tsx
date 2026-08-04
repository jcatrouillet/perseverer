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
    <form onSubmit={(e) => void handleSubmit(e)}>
      <label>
        Username
        <input
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          required
        />
      </label>
      <label>
        Password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
      </label>
      {error && <p role="alert">{error}</p>}
      <button type="submit" disabled={submitting}>
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
    <form onSubmit={handleSubmit}>
      <label>
        API key
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="generated via `sync athlete create-key`"
          required
        />
      </label>
      <button type="submit">Use this key</button>
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
    <main>
      <h1>Sport Health Data Platform</h1>
      <nav>
        <button type="button" onClick={() => setTab("login")} disabled={tab === "login"}>
          Log in
        </button>
        <button type="button" onClick={() => setTab("api-key")} disabled={tab === "api-key"}>
          Use an API key
        </button>
      </nav>
      {tab === "login" ? (
        <LoginForm onSuccess={() => setAuthenticated(true)} />
      ) : (
        <ApiKeyForm onSuccess={() => setAuthenticated(true)} />
      )}
    </main>
  );
}
