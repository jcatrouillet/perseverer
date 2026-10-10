// Wraps the whole app. Renders children once a credential is stored; otherwise a two-tab
// form -- password login (issues a JWT) or a pasted per-athlete API key (see
// sync athlete set-password / create-key). Re-shows itself if a background request clears the
// stored credential (AUTH_CLEARED_EVENT), e.g. an expired JWT. Also hosts the forgotten-password
// flow: "Forgot your password?" emails a reset link, and that link (/reset-password?token=...)
// opens the "choose a new password" form here, signed in or not. see docs/ARCHITECTURE.md.
import { useEffect, useState } from "react";

import {
  AUTH_CLEARED_EVENT,
  AuthError,
  hasStoredCredential,
  login,
  requestPasswordReset,
  resetPassword,
  ResetLinkError,
  ServerUnconfiguredError,
  storeApiKey,
  storeJwt,
} from "../api/client";
import "../styles/auth.css";
import { ThemeToggle } from "./ThemeToggle";

type Tab = "login" | "api-key";

const RESET_PATH = "/reset-password";
const MIN_PASSWORD_LENGTH = 8;

/** The token of the reset link this page was opened with, if any. */
function resetTokenFromUrl(): string | null {
  if (window.location.pathname !== RESET_PATH) return null;
  return new URLSearchParams(window.location.search).get("token");
}

function LoginForm({
  onSuccess,
  onForgotPassword,
}: {
  onSuccess: () => void;
  onForgotPassword: () => void;
}) {
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
        setError("Server is not configured for password login (PERSEVERER_JWT_SECRET unset).");
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
      <button type="button" className="auth-form__link" onClick={onForgotPassword}>
        Forgot your password?
      </button>
    </form>
  );
}

function ForgotPasswordForm({ onBack }: { onBack: () => void }) {
  const [identifier, setIdentifier] = useState("");
  const [result, setResult] = useState<"sent" | "no-email" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const { email_configured } = await requestPasswordReset(identifier.trim());
      setResult(email_configured ? "sent" : "no-email");
    } catch {
      setError("Could not send the request. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return (
      <div className="auth-form">
        <p className="auth-form__notice" role="status">
          {result === "sent" ? (
            <>
              If an account matches, a link to choose a new password is on its way to the email
              address in its profile. The link works once, for one hour.
            </>
          ) : (
            <>
              This server isn&apos;t set up to send email, so the password can&apos;t be reset from
              here. The server&apos;s administrator can set a new one with{" "}
              <code>sync athlete set-password</code>.
            </>
          )}
        </p>
        <button type="button" className="button button--full" onClick={onBack}>
          Back to log in
        </button>
      </div>
    );
  }
  return (
    <form className="auth-form" onSubmit={(e) => void handleSubmit(e)}>
      <p className="auth-form__hint">
        Enter your username or email address and we&apos;ll email you a link to choose a new
        password.
      </p>
      <label className="field">
        Username or email
        <input
          className="input"
          type="text"
          value={identifier}
          onChange={(e) => setIdentifier(e.target.value)}
          autoComplete="username"
          required
        />
      </label>
      {error && (
        <p className="auth-form__error" role="alert">
          {error}
        </p>
      )}
      <button className="button button--primary button--full" type="submit" disabled={submitting}>
        {submitting ? "Sending…" : "Send reset link"}
      </button>
      <button type="button" className="auth-form__link" onClick={onBack}>
        Back to log in
      </button>
    </form>
  );
}

function ResetPasswordForm({ token, onDone }: { token: string; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    if (password !== confirm) {
      setError("The two passwords don't match.");
      return;
    }
    setSubmitting(true);
    try {
      await resetPassword(token, password);
      setDone(true);
    } catch (err) {
      setError(
        err instanceof ResetLinkError
          ? "This link is invalid, expired or already used. Ask for a new one from the log-in page."
          : "Could not change the password. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="auth-form">
        <p className="auth-form__notice" role="status">
          Your password has been changed. Log in with the new one.
        </p>
        <button type="button" className="button button--primary button--full" onClick={onDone}>
          Go to log in
        </button>
      </div>
    );
  }
  return (
    <form className="auth-form" onSubmit={(e) => void handleSubmit(e)}>
      <label className="field">
        New password
        <input
          className="input"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          required
        />
      </label>
      <label className="field">
        Confirm new password
        <input
          className="input"
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
          required
        />
      </label>
      {error && (
        <p className="auth-form__error" role="alert">
          {error}
        </p>
      )}
      <button className="button button--primary button--full" type="submit" disabled={submitting}>
        {submitting ? "Saving…" : "Set new password"}
      </button>
      <button type="button" className="auth-form__link" onClick={onDone}>
        Back to log in
      </button>
    </form>
  );
}

function ApiKeyForm({ onSuccess }: { onSuccess: () => void }) {
  const [apiKey, setApiKey] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!apiKey.trim()) return;
    // No validation round-trip here by design -- the next real API call
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
  const [forgot, setForgot] = useState(false);
  const [resetToken, setResetToken] = useState(resetTokenFromUrl);

  useEffect(() => {
    function onAuthCleared() {
      setAuthenticated(false);
    }
    window.addEventListener(AUTH_CLEARED_EVENT, onAuthCleared);
    return () => window.removeEventListener(AUTH_CLEARED_EVENT, onAuthCleared);
  }, []);

  // Take the token out of the address bar (and the browser history) once it has been read.
  useEffect(() => {
    if (resetToken) window.history.replaceState(null, "", RESET_PATH);
  }, [resetToken]);

  function leaveReset() {
    setResetToken(null);
    setForgot(false);
    window.history.replaceState(null, "", "/");
  }

  if (authenticated && !resetToken) {
    return <>{children}</>;
  }

  let subtitle = "Sign in to view your training and health data";
  let content: React.ReactNode;
  if (resetToken) {
    subtitle = "Choose a new password";
    content = <ResetPasswordForm token={resetToken} onDone={leaveReset} />;
  } else if (forgot) {
    subtitle = "Reset your password";
    content = <ForgotPasswordForm onBack={() => setForgot(false)} />;
  } else {
    content = (
      <>
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
          <LoginForm
            onSuccess={() => setAuthenticated(true)}
            onForgotPassword={() => setForgot(true)}
          />
        ) : (
          <ApiKeyForm onSuccess={() => setAuthenticated(true)} />
        )}
      </>
    );
  }

  return (
    <div className="auth-page">
      <div className="auth-page__toggle">
        <ThemeToggle />
      </div>
      <main className="auth-card">
        <div className="auth-card__brand">
          <div className="auth-card__logo">P</div>
          <h1>Perseverer</h1>
          <p className="auth-card__subtitle">{subtitle}</p>
        </div>
        {content}
      </main>
    </div>
  );
}
