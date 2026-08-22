// The athlete's own logged carbohydrate/sodium intake for a run -- no vendor source carries this
// at all (see sport_override.py's own docstring: neither the FIT profile nor the Garmin Connect
// API define it), so unlike ActivitySportCorrection/ActivityNameCorrection this isn't fixing a
// wrong value, it's the athlete's only input. Mirrors ActivityNameCorrection's inline-edit-form
// UX, just with two numeric fields submitted together (like sport/sub_sport) rather than one.
import { useState } from "react";

export function ActivityFueling({
  carbohydratesG,
  sodiumMg,
  onSubmit,
  isSubmitting,
  isError,
}: {
  carbohydratesG: number | null;
  sodiumMg: number | null;
  onSubmit: (values: { carbohydrates_g: number | null; sodium_mg: number | null }) => void;
  isSubmitting: boolean;
  isError: boolean;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [carbs, setCarbs] = useState("");
  const [sodium, setSodium] = useState("");

  const hasData = carbohydratesG != null || sodiumMg != null;

  if (!isEditing) {
    return (
      <div className="activity-detail__fueling">
        <h3 className="activity-detail__fueling-heading">Fueling</h3>
        <p className="activity-detail__fueling-row">
          <span className="activity-detail__fueling-readout">
            {hasData ? (
              <>
                {carbohydratesG != null && <span>{carbohydratesG}g carbs</span>}
                {sodiumMg != null && <span>{sodiumMg}mg sodium</span>}
              </>
            ) : (
              <span>Nothing logged</span>
            )}
          </span>
          <button
            type="button"
            className="activity-detail__sport-fix-btn"
            onClick={() => {
              setCarbs(carbohydratesG != null ? String(carbohydratesG) : "");
              setSodium(sodiumMg != null ? String(sodiumMg) : "");
              setIsEditing(true);
            }}
          >
            {hasData ? "Edit" : "Add fueling data"}
          </button>
        </p>
      </div>
    );
  }

  return (
    <div className="activity-detail__fueling">
      <h3 className="activity-detail__fueling-heading">Fueling</h3>
      <span className="activity-detail__sport-fix">
        <input
          type="number"
          min="0"
          step="any"
          inputMode="decimal"
          value={carbs}
          onChange={(e) => setCarbs(e.target.value)}
          placeholder="Carbs (g)"
          aria-label="Carbohydrates in grams"
        />
        <input
          type="number"
          min="0"
          step="any"
          inputMode="decimal"
          value={sodium}
          onChange={(e) => setSodium(e.target.value)}
          placeholder="Sodium (mg)"
          aria-label="Sodium in milligrams"
        />
        <button
          type="button"
          disabled={isSubmitting}
          onClick={() => {
            onSubmit({
              carbohydrates_g: carbs.trim() === "" ? null : Number(carbs),
              sodium_mg: sodium.trim() === "" ? null : Number(sodium),
            });
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
    </div>
  );
}
