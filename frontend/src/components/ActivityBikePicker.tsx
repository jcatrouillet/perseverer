// The bike a ride was made on, shown in a ride's meta line with a way to change it. The bike is
// the one chosen for this ride, else the default for its category; the tires on the bike then
// inherit the ride's distance. Only cycling activities have one.
import { useState } from "react";

import { useActivityBike, useBikes, useSetActivityBike } from "../api/queries";

export function ActivityBikePicker({
  activityId,
  isRide,
}: {
  activityId: string;
  isRide: boolean;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const bikes = useBikes();
  const selected = useActivityBike(activityId, isRide);
  const setBike = useSetActivityBike(activityId);
  if (!isRide || bikes.isLoading || selected.isLoading) return null;
  // Nothing to choose from, and nothing chosen: don't clutter the page of someone without bikes.
  if (!selected.data?.bike_id && (bikes.data ?? []).length === 0) return null;

  const bikeId = selected.data?.bike_id ?? null;
  const bike = (bikes.data ?? []).find((candidate) => candidate.id === bikeId);

  return (
    <span className="activity-detail__shoe" aria-label="Activity bike">
      {" · "}
      {isEditing ? (
        <label className="field">
          Bike
          <select
            value={bikeId ?? ""}
            onChange={(event) =>
              setBike.mutate(event.target.value || null, {
                onSuccess: () => setIsEditing(false),
              })
            }
            disabled={setBike.isPending}
          >
            <option value="">{bikeId ? "Back to the default" : "Choose a bike"}</option>
            {(bikes.data ?? []).map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.brand} {candidate.model}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <>
          {bike ? `Bike: ${bike.brand} ${bike.model}` : "Bike"}
          <button
            type="button"
            className="activity-detail__sport-fix-btn"
            onClick={() => setIsEditing(true)}
          >
            {bikeId ? "Edit" : "Add"}
          </button>
        </>
      )}
      {setBike.isError && (
        <span className="form-error" role="alert">
          Could not save the bike.
        </span>
      )}
    </span>
  );
}
