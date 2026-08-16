// "Not the right title? Fix it" -- a manual correction for activities whose title is a generic
// on-device default (see routers/activities.py::override_activity_name's own docstring: Garmin
// Connect's own name isn't reliably a real athlete-given title either, so there's no automatic
// fix for this). Mirrors ActivitySportCorrection's split-control UX: a plain link that reveals a
// small inline form, not a modal.
import { useState } from "react";

export function ActivityNameCorrection({
  currentName,
  onSubmit,
  isSubmitting,
  isError,
}: {
  currentName: string | null;
  onSubmit: (name: string) => void;
  isSubmitting: boolean;
  isError: boolean;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [value, setValue] = useState(currentName ?? "");

  if (!isEditing) {
    return (
      <button
        type="button"
        className="activity-detail__sport-fix-btn"
        onClick={() => {
          setValue(currentName ?? "");
          setIsEditing(true);
        }}
      >
        Not the right title? Fix it
      </button>
    );
  }

  return (
    <span className="activity-detail__sport-fix">
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Activity title"
      />
      <button
        type="button"
        disabled={isSubmitting || !value.trim()}
        onClick={() => {
          onSubmit(value.trim());
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
