// Settings page: publish/rotate/unpublish a Google-Calendar-subscribable iCalendar feed of the
// athlete's own planned_workout calendar. See calendar_feed.py's own module docstring -- a single
// standing per-athlete secret (mirroring athlete.api_key_hash), not a share_link-style growing
// history, so there's no "list my feeds" concept, just publish/rotate/unpublish one link.
import { useState } from "react";

import {
  useCalendarFeedStatus,
  usePublishCalendarFeed,
  useUnpublishCalendarFeed,
} from "../api/queries";
import "../styles/share-button.css";

export function CalendarFeedCard() {
  const status = useCalendarFeedStatus();
  const publish = usePublishCalendarFeed();
  const unpublish = useUnpublishCalendarFeed();
  const [copied, setCopied] = useState(false);

  const enabled = status.data?.enabled ?? false;
  const url = publish.data?.url;

  async function handleCopy(value: string) {
    await navigator.clipboard.writeText(value);
    setCopied(true);
  }

  return (
    <section className="card">
      <h2>Publish calendar</h2>
      <p className="chart-note">
        Publishes your planned workouts as a calendar feed anyone with the link can view — without
        signing in. In Google Calendar: Settings → Add calendar → From URL, then paste the link
        below.
      </p>
      <button
        className="button"
        type="button"
        disabled={publish.isPending || status.isLoading}
        onClick={() => {
          setCopied(false);
          publish.mutate();
        }}
      >
        {publish.isPending ? "Publishing…" : enabled ? "Rotate link" : "Publish calendar"}
      </button>
      {enabled && (
        <button
          className="button"
          type="button"
          disabled={unpublish.isPending}
          onClick={() => unpublish.mutate()}
        >
          {unpublish.isPending ? "Stopping…" : "Stop publishing"}
        </button>
      )}
      {publish.isError && (
        <p role="alert" className="share-button__error">
          Could not publish the calendar feed.
        </p>
      )}
      {url && (
        <div className="share-button__url-row">
          <input
            className="input"
            type="text"
            value={url}
            readOnly
            onFocus={(e) => e.target.select()}
          />
          <button
            className="button button--primary"
            type="button"
            onClick={() => void handleCopy(url)}
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      )}
    </section>
  );
}
