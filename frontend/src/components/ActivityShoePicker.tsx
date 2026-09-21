import { useActivityShoe, useSetActivityShoe, useShoes } from "../api/queries";

export function ActivityShoePicker({
  activityId,
  hasDistance,
}: {
  activityId: string;
  hasDistance: boolean;
}) {
  const shoes = useShoes();
  const selected = useActivityShoe(activityId, hasDistance);
  const setShoe = useSetActivityShoe(activityId);
  if (!hasDistance || shoes.isLoading || selected.isLoading) return null;
  return (
    <section className="activity-shoe card" aria-label="Shoes">
      <h2>Shoes</h2>
      <p>
        Choose the pair used for this activity. Its distance will be added to that pair’s mileage.
      </p>
      <label className="field">
        Pair
        <select
          value={selected.data?.shoe_id ?? ""}
          onChange={(event) => setShoe.mutate(event.target.value || null)}
          disabled={setShoe.isPending}
        >
          <option value="">No pair selected</option>
          {(shoes.data ?? []).map((shoe) => (
            <option key={shoe.id} value={shoe.id}>
              {shoe.brand} {shoe.model}
            </option>
          ))}
        </select>
      </label>
      {setShoe.isError && (
        <p className="form-error" role="alert">
          Could not save the shoe selection.
        </p>
      )}
    </section>
  );
}
