// Settings page: generate/rotate/revoke the athlete's own standing personal API key -- the
// self-service counterpart of `sync athlete create-key`. Same one-standing-secret-per-athlete
// shape as CalendarFeedCard.tsx (mirroring athlete.api_key_hash), so the create/rotate/revoke
// wiring is deliberately identical; the one addition here is a reveal toggle, since a raw API
// key is more sensitive to leave on-screen (a screenshot, a shoulder-surf) than a calendar
// subscription link is.
import { useState } from "react";

import { useApiKeyStatus, useCreateApiKey, useDeleteApiKey } from "../api/queries";
import "../styles/share-button.css";

export function ApiKeyCard() {
  const status = useApiKeyStatus();
  const create = useCreateApiKey();
  const revoke = useDeleteApiKey();
  const [copied, setCopied] = useState(false);
  const [revealed, setRevealed] = useState(false);

  const enabled = status.data?.enabled ?? false;
  const apiKey = create.data?.api_key;

  async function handleCopy(value: string) {
    await navigator.clipboard.writeText(value);
    setCopied(true);
  }

  return (
    <section className="card">
      <h2>API key</h2>
      <p className="chart-note">
        A personal key for scripts or the MCP server to authenticate as you (send it as{" "}
        <code>X-API-Key</code>). Scoped to your own data only — it can never read or change another
        athlete&apos;s. Generating a new key immediately invalidates the old one.
      </p>
      <button
        className="button"
        type="button"
        disabled={create.isPending || status.isLoading}
        onClick={() => {
          setCopied(false);
          setRevealed(false);
          create.mutate();
        }}
      >
        {create.isPending ? "Generating…" : enabled ? "Rotate key" : "Generate API key"}
      </button>
      {enabled && (
        <button
          className="button"
          type="button"
          disabled={revoke.isPending}
          onClick={() => revoke.mutate()}
        >
          {revoke.isPending ? "Revoking…" : "Revoke key"}
        </button>
      )}
      {create.isError && (
        <p role="alert" className="share-button__error">
          Could not generate an API key.
        </p>
      )}
      {apiKey && (
        <>
          <p className="chart-note">
            Copy this now — it won&apos;t be shown again after you leave this page.
          </p>
          <div className="share-button__url-row">
            <input
              className="input"
              type={revealed ? "text" : "password"}
              value={apiKey}
              readOnly
              onFocus={(e) => e.target.select()}
            />
            <button className="button" type="button" onClick={() => setRevealed((r) => !r)}>
              {revealed ? "Hide" : "Show"}
            </button>
            <button
              className="button button--primary"
              type="button"
              onClick={() => void handleCopy(apiKey)}
            >
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
