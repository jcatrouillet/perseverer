// "Not the right sport? Fix it" -- a manual correction for activities whose raw source itself
// records the wrong sport (see routers/activities.py::override_activity_sport's own docstring:
// e.g. a hike recorded with a watch's "Run" profile still selected, or a FIT file reconstructed
// by a third-party tool that always writes `sport=running` regardless of what the activity
// actually was). Mirrors ActivitySourcesPanel's "Not the same activity?" split control: a plain
// link that reveals a small inline form, not a modal.
import { useState } from "react";

import { KNOWN_SPORTS } from "../metricStyle";

export function ActivitySportCorrection({
  currentSport,
  onSubmit,
  isSubmitting,
  isError,
}: {
  currentSport: string;
  onSubmit: (sport: string) => void;
  isSubmitting: boolean;
  isError: boolean;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [selected, setSelected] = useState(currentSport);

  if (!isEditing) {
    return (
      <button
        type="button"
        className="activity-detail__sport-fix-btn"
        onClick={() => {
          setSelected(currentSport);
          setIsEditing(true);
        }}
      >
        Not the right sport? Fix it
      </button>
    );
  }

  return (
    <span className="activity-detail__sport-fix">
      <select value={selected} onChange={(e) => setSelected(e.target.value)}>
        {KNOWN_SPORTS.map((s) => (
          <option key={s} value={s}>
            {s.replace(/_/g, " ")}
          </option>
        ))}
      </select>
      <button
        type="button"
        disabled={isSubmitting}
        onClick={() => {
          onSubmit(selected);
          setIsEditing(false);
        }}
      >
        {isSubmitting ? "Saving…" : "Save"}
      </button>
      <button type="button" onClick={() => setIsEditing(false)}>
        Cancel
      </button>
      {isError && (
        <span role="alert" className="activity-detail__sport-fix-error">
          Could not save.
        </span>
      )}
    </span>
  );
}
