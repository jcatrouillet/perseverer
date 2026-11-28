// A popup shown when the app is opened on a phone right after an activity: the last activity
// that finished within the past 8 hours, once per activity. Mounted once in the app shell;
// not shown while a coach views an athlete, nor on the activity's own page.
import { useState } from "react";
import { Link, useLocation } from "wouter";

import { useActivities, useActivityRoutes } from "../api/queries";
import { localIsoDate } from "../dateUtils";
import { lastFinishedWithin, SHOWN_STORAGE_KEY } from "../recentActivity";
import { useIsMobile } from "../useIsMobile";
import { ActivityCard } from "./ActivityCard";
import { Modal } from "./Modal";
import "../styles/recent-activity.css";

// The last activity the popup was dismissed for. Kept in localStorage, not sessionStorage: an
// installed phone app can stay alive in the background for days, so "once per session" would hide
// a new activity; "once per activity" never does.
function alreadyShown(id: string): boolean {
  try {
    return window.localStorage.getItem(SHOWN_STORAGE_KEY) === id;
  } catch {
    return false;
  }
}

function markShown(id: string): void {
  try {
    window.localStorage.setItem(SHOWN_STORAGE_KEY, id);
  } catch {
    // Storage blocked: the popup may reappear on the next open, which is harmless.
  }
}

export function RecentActivityPopup() {
  const isMobile = useIsMobile();
  const [location] = useLocation();
  // The activity whose popup was closed in this page's lifetime (a newer one may still show).
  const [closedId, setClosedId] = useState<string | null>(null);
  // Two local dates back always covers 8 hours, whatever the offset; the exact window is applied
  // in lastFinishedWithin below.
  const twoDaysAgo = new Date();
  twoDaysAgo.setDate(twoDaysAgo.getDate() - 2);
  const recent = useActivities({ startDate: localIsoDate(twoDaysAgo), limit: 20 });
  const last = isMobile && recent.data ? lastFinishedWithin(recent.data.items, new Date()) : null;
  const routes = useActivityRoutes(last ? [last.id] : []);

  if (!last || closedId === last.id || alreadyShown(last.id)) return null;
  if (location === `/activities/${last.id}`) return null;

  const close = () => {
    markShown(last.id);
    setClosedId(last.id);
  };

  return (
    <Modal open onClose={close} title="Just finished" panelClassName="recent-activity">
      <ActivityCard
        activity={last}
        encodedPolyline={routes.data?.[0]?.simplified_polyline ?? undefined}
      />
      <div className="recent-activity__actions">
        <button type="button" className="button" onClick={close}>
          Close
        </button>
        <Link
          href={`/activities/${last.id}`}
          className="button button--primary"
          onClick={() => markShown(last.id)}
        >
          Open activity
        </Link>
      </div>
    </Modal>
  );
}
