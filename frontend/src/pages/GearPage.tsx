import { FormEvent, useState } from "react";

import {
  useAllActivities,
  useCreateShoe,
  useRetireShoe,
  useSetDefaultShoe,
  useShoes,
} from "../api/queries";
import { Icon } from "../components/Icon";
import "../styles/gear.css";

const initial = {
  brand: "",
  model: "",
  size: "",
  comments: "",
  initialDistance: "0",
  maxDistance: "800",
  defaultSports: [] as string[],
};

export function GearPage() {
  const [form, setForm] = useState(initial);
  const [showRetired, setShowRetired] = useState(false);
  const shoes = useShoes(showRetired);
  const activities = useAllActivities({});
  const create = useCreateShoe();
  const setDefault = useSetDefaultShoe();
  const retire = useRetireShoe();
  const sports = [
    ...new Set((activities.data ?? []).filter((a) => (a.distance_m ?? 0) > 0).map((a) => a.sport)),
  ].sort();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const defaultSports = form.defaultSports;
    create.mutate(
      {
        brand: form.brand,
        model: form.model,
        size: form.size || null,
        comments: form.comments || null,
        initial_distance_km: Number(form.initialDistance),
        max_distance_km: form.maxDistance ? Number(form.maxDistance) : null,
      },
      {
        onSuccess: (shoe) => {
          defaultSports.forEach((sport) => setDefault.mutate({ sport, shoeId: shoe.id }));
          setForm(initial);
        },
      },
    );
  };

  return (
    <section className="gear-page">
      <div className="gear-page__heading">
        <span className="icon-chip icon-chip--lg">
          <Icon name="shoe" />
        </span>
        <div>
          <h1>Gear</h1>
          <p className="gear-page__intro">Track shoe mileage, defaults, and replacement limits.</p>
        </div>
      </div>

      <section className="card gear-card">
        <h2>Add shoes</h2>
        <p className="gear-card__description">
          Initial distance includes kilometres already used before adding the pair.
        </p>
        <form className="gear-form" onSubmit={submit}>
          <label className="field">
            Brand
            <input
              className="input"
              required
              value={form.brand}
              onChange={(e) => setForm({ ...form, brand: e.target.value })}
            />
          </label>
          <label className="field">
            Model
            <input
              className="input"
              required
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
            />
          </label>
          <label className="field gear-form__size">
            Size
            <input
              className="input"
              value={form.size}
              onChange={(e) => setForm({ ...form, size: e.target.value })}
            />
          </label>
          <label className="field">
            Initial distance (km)
            <input
              className="input"
              type="number"
              min="0"
              step="0.1"
              required
              value={form.initialDistance}
              onChange={(e) => setForm({ ...form, initialDistance: e.target.value })}
            />
          </label>
          <label className="field">
            Maximum distance (km)
            <input
              className="input"
              type="number"
              min="0"
              step="0.1"
              placeholder="No limit"
              value={form.maxDistance}
              onChange={(e) => setForm({ ...form, maxDistance: e.target.value })}
            />
            <span className="gear-form__hint">Leave empty for no limit</span>
          </label>
          {sports.length > 0 && (
            <fieldset className="gear-form__sports">
              <legend>Use as default for</legend>
              <div className="gear-sport-options">
                {sports.map((sport) => (
                  <label
                    className={
                      form.defaultSports.includes(sport)
                        ? "gear-sport-option is-selected"
                        : "gear-sport-option"
                    }
                    key={sport}
                  >
                    <input
                      type="checkbox"
                      checked={form.defaultSports.includes(sport)}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          defaultSports: e.target.checked
                            ? [...form.defaultSports, sport]
                            : form.defaultSports.filter((item) => item !== sport),
                        })
                      }
                    />
                    <span>{sport.replace(/_/g, " ")}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          <label className="field gear-form__comments">
            Comments
            <textarea
              className="input"
              value={form.comments}
              onChange={(e) => setForm({ ...form, comments: e.target.value })}
            />
          </label>
          <div className="gear-form__actions">
            <button className="button button--primary" disabled={create.isPending}>
              <Icon name="shoe" />
              {create.isPending ? "Adding…" : "Add shoes"}
            </button>
          </div>
          {create.isError && (
            <p className="form-error" role="alert">
              Could not add this pair.
            </p>
          )}
        </form>
      </section>

      <section className="card gear-defaults">
        <h2>Default shoes by sport</h2>
        <p className="gear-card__description">
          New distance activities use the selected pair automatically.
        </p>
        {sports.length === 0 ? (
          <p className="gear-empty">
            Distance activities are needed before sports can be configured.
          </p>
        ) : (
          <div className="gear-defaults__grid">
            {sports.map((sport) => (
              <label className="gear-default-row" key={sport}>
                <span>{sport.replace(/_/g, " ")}</span>
                <select
                  className="input"
                  value={
                    (shoes.data ?? []).find((shoe) => shoe.default_sports.includes(sport))?.id ?? ""
                  }
                  onChange={(e) => {
                    if (e.target.value) setDefault.mutate({ sport, shoeId: e.target.value });
                  }}
                  disabled={setDefault.isPending}
                >
                  <option value="">No default pair</option>
                  {(shoes.data ?? [])
                    .filter((shoe) => !shoe.retired)
                    .map((shoe) => (
                      <option key={shoe.id} value={shoe.id}>
                        {shoe.brand} {shoe.model}
                      </option>
                    ))}
                </select>
              </label>
            ))}
          </div>
        )}
      </section>

      <section className="gear-inventory">
        <div className="gear-inventory__heading">
          <h2>Your shoes</h2>
          <label className="gear-show-retired">
            <input
              type="checkbox"
              checked={showRetired}
              onChange={(e) => setShowRetired(e.target.checked)}
            />
            Show retired
          </label>
        </div>
        {shoes.isLoading ? (
          <p className="gear-empty">Loading shoes…</p>
        ) : shoes.data?.length ? (
          <div className="gear-list">
            {shoes.data.map((shoe) => (
              <article
                className={shoe.over_limit ? "gear-shoe gear-shoe--limit" : "gear-shoe"}
                key={shoe.id}
              >
                <div className="gear-shoe__header">
                  <span className="icon-chip">
                    <Icon name="shoe" />
                  </span>
                  <div>
                    <h3>
                      {shoe.brand} {shoe.model}
                    </h3>
                    <div className="gear-shoe__badges">
                      {shoe.retired && <span className="badge">Retired</span>}
                      {shoe.size && <span className="badge">Size {shoe.size}</span>}
                      {shoe.default_sports.map((sport) => (
                        <span className="badge" key={sport}>
                          {sport.replace(/_/g, " ")}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="gear-shoe__mileage">
                  <strong>{shoe.distance_km.toFixed(1)} km</strong>
                  <span>
                    {shoe.max_distance_km == null
                      ? "No mileage limit"
                      : shoe.over_limit
                        ? `Limit of ${shoe.max_distance_km.toFixed(0)} km reached`
                        : `${shoe.remaining_km?.toFixed(1)} km remaining`}
                  </span>
                  {shoe.max_distance_km != null && (
                    <progress
                      max={shoe.max_distance_km}
                      value={Math.min(shoe.distance_km, shoe.max_distance_km)}
                    />
                  )}
                </div>
                {shoe.comments && <p className="gear-shoe__comments">{shoe.comments}</p>}
                {!shoe.retired && (
                  <button
                    className="button"
                    onClick={() => retire.mutate(shoe.id)}
                    disabled={retire.isPending}
                  >
                    Retire shoes
                  </button>
                )}
              </article>
            ))}
          </div>
        ) : (
          <div className="card gear-empty">
            <span className="icon-chip icon-chip--lg">
              <Icon name="shoe" />
            </span>
            <h3>No shoes added yet</h3>
            <p>Add your first pair above to start tracking its mileage.</p>
          </div>
        )}
      </section>
    </section>
  );
}
