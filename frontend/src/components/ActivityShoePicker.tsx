import { useState } from "react";

import { useActivityShoe, useSetActivityShoe, useShoes } from "../api/queries";

export function ActivityShoePicker({
  activityId,
  hasDistance,
}: {
  activityId: string;
  hasDistance: boolean;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const shoes = useShoes();
  const selected = useActivityShoe(activityId, hasDistance);
  const setShoe = useSetActivityShoe(activityId);
  if (!hasDistance || shoes.isLoading || selected.isLoading) return null;

  const shoeId = selected.data?.shoe_id ?? null;
  const shoe = (shoes.data ?? []).find((candidate) => candidate.id === shoeId);

  return (
    <span className="activity-detail__shoe" aria-label="Activity shoes">
      {" · "}
      {isEditing ? (
        <label className="field">
          Shoes
          <select
            value={shoeId ?? ""}
            onChange={(event) =>
              setShoe.mutate(event.target.value || null, {
                onSuccess: () => setIsEditing(false),
              })
            }
            disabled={setShoe.isPending}
          >
            <option value="">Choose shoes</option>
            {(shoes.data ?? []).map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.brand} {candidate.model}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <>
          {shoe ? `Shoes: ${shoe.brand} ${shoe.model}` : "Shoes"}
          <button
            type="button"
            className="activity-detail__sport-fix-btn"
            onClick={() => setIsEditing(true)}
          >
            {shoeId ? "Edit" : "Add"}
          </button>
        </>
      )}
      {setShoe.isError && (
        <span className="form-error" role="alert">
          Could not save the shoe selection.
        </span>
      )}
    </span>
  );
}
