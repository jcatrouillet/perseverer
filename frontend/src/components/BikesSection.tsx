// The bikes half of the Gear page: each bike with its distance, the cycling categories it is the
// default for, and its front and rear tire (each with a limit and a progress bar); spare tires
// that are not on any bike; and the popups to add a bike or a tire. A bike without tires is fine
// (an indoor trainer bike, say). Tires are separate from bikes: they can be replaced, swapped to
// another bike, taken off and retired, and each keeps the distance of the rides made while it was on.
import { FormEvent, useState } from "react";

import {
  useBikes,
  useClearDefaultBike,
  useCreateBike,
  useCreateTire,
  useInstallTire,
  useRemoveTire,
  useRetireBike,
  useRetireTire,
  useSetDefaultBike,
  useTires,
} from "../api/queries";
import type { BikeCategory, BikeOut, TireOut, TirePosition } from "../api/types";
import { BIKE_CATEGORIES, DEFAULT_TIRE_LIMIT_KM, bikeCategoryLabel } from "../bikeCategories";
import { Icon } from "./Icon";
import { Modal } from "./Modal";

const POSITIONS: TirePosition[] = ["front", "rear"];

function TireCard({
  tire,
  position,
  actions,
}: {
  tire: TireOut;
  position?: TirePosition;
  actions: React.ReactNode;
}) {
  return (
    <div className={tire.over_limit ? "gear-tire gear-tire--limit" : "gear-tire"}>
      <div className="gear-tire__title">
        {position && <span className="badge">{position === "front" ? "Front" : "Rear"}</span>}
        <strong>
          {tire.brand} {tire.model}
        </strong>
        {tire.size && <span className="badge">{tire.size}</span>}
      </div>
      <div className="gear-shoe__mileage">
        <strong>{tire.distance_km.toFixed(0)} km</strong>
        <span>
          {tire.max_distance_km == null
            ? "No mileage limit"
            : tire.over_limit
              ? `Limit of ${tire.max_distance_km.toFixed(0)} km reached`
              : `${tire.remaining_km?.toFixed(0)} km remaining`}
        </span>
        {tire.max_distance_km != null && (
          <progress
            max={tire.max_distance_km}
            value={Math.min(tire.distance_km, tire.max_distance_km)}
          />
        )}
      </div>
      {tire.comments && <p className="gear-shoe__comments">{tire.comments}</p>}
      <div className="gear-tire__actions">{actions}</div>
    </div>
  );
}

/** The form that adds a tire, fitted to a bike's slot when `bikeId` and `position` are given. */
function TireForm({
  bikeId,
  position,
  onDone,
}: {
  bikeId?: string;
  position?: TirePosition;
  onDone: () => void;
}) {
  const create = useCreateTire();
  const [form, setForm] = useState({
    brand: "",
    model: "",
    size: "",
    comments: "",
    initial: "0",
    max: DEFAULT_TIRE_LIMIT_KM,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      {
        brand: form.brand,
        model: form.model,
        size: form.size || null,
        comments: form.comments || null,
        initial_distance_km: Number(form.initial),
        max_distance_km: form.max ? Number(form.max) : null,
        bike_id: bikeId ?? null,
        position: position ?? null,
      },
      { onSuccess: onDone },
    );
  };
  return (
    <form className="gear-form gear-tire-form" onSubmit={submit}>
      <label className="field">
        Tire brand
        <input
          className="input"
          required
          value={form.brand}
          onChange={(e) => setForm({ ...form, brand: e.target.value })}
        />
      </label>
      <label className="field">
        Tire model
        <input
          className="input"
          required
          value={form.model}
          onChange={(e) => setForm({ ...form, model: e.target.value })}
        />
      </label>
      <label className="field gear-form__size">
        Tire size
        <input
          className="input"
          placeholder="700x28"
          value={form.size}
          onChange={(e) => setForm({ ...form, size: e.target.value })}
        />
      </label>
      <label className="field">
        Tire initial distance (km)
        <input
          className="input"
          type="number"
          min="0"
          step="1"
          required
          value={form.initial}
          onChange={(e) => setForm({ ...form, initial: e.target.value })}
        />
      </label>
      <label className="field">
        Tire maximum distance (km)
        <input
          className="input"
          type="number"
          min="1"
          step="1"
          placeholder="No limit"
          value={form.max}
          onChange={(e) => setForm({ ...form, max: e.target.value })}
        />
      </label>
      <label className="field gear-form__comments">
        Tire comments
        <textarea
          className="input"
          value={form.comments}
          onChange={(e) => setForm({ ...form, comments: e.target.value })}
        />
      </label>
      <div className="gear-form__actions">
        <button className="button button--primary" disabled={create.isPending}>
          {position ? `Fit ${position} tire` : "Add tire"}
        </button>{" "}
        <button type="button" className="button" onClick={onDone}>
          Cancel
        </button>
      </div>
      {create.isError && (
        <p className="form-error" role="alert">
          Could not add this tire.
        </p>
      )}
    </form>
  );
}

function TireSlot({
  bike,
  position,
  tire,
  spares,
}: {
  bike: BikeOut;
  position: TirePosition;
  tire: TireOut | null;
  spares: TireOut[];
}) {
  const [adding, setAdding] = useState(false);
  const install = useInstallTire();
  const remove = useRemoveTire();
  const retire = useRetireTire();
  const label = position === "front" ? "front" : "rear";

  return (
    <div className="gear-slot">
      {tire && (
        <TireCard
          tire={tire}
          position={position}
          actions={
            <>
              <button type="button" className="button" onClick={() => setAdding(true)}>
                Replace
              </button>
              <button
                type="button"
                className="button"
                disabled={remove.isPending}
                onClick={() => remove.mutate(tire.id)}
              >
                Take off
              </button>
              <button
                type="button"
                className="button"
                disabled={retire.isPending}
                onClick={() => retire.mutate(tire.id)}
              >
                Retire tire
              </button>
            </>
          }
        />
      )}
      {!tire && (
        <div className="gear-slot__empty">
          <span className="badge">{position === "front" ? "Front" : "Rear"}</span>
          <span className="gear-empty">No {label} tire</span>
          <button type="button" className="button" onClick={() => setAdding(true)}>
            + Add {label} tire
          </button>
          {spares.length > 0 && (
            <select
              className="input"
              aria-label={`Fit a spare ${label} tire to ${bike.brand} ${bike.model}`}
              value=""
              onChange={(e) => {
                if (e.target.value)
                  install.mutate({ tireId: e.target.value, bikeId: bike.id, position });
              }}
              disabled={install.isPending}
            >
              <option value="">Fit a spare…</option>
              {spares.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.brand} {s.model}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
      <Modal
        open={adding}
        onClose={() => setAdding(false)}
        title={tire ? `Replace the ${label} tire` : `Add a ${label} tire`}
      >
        <TireForm bikeId={bike.id} position={position} onDone={() => setAdding(false)} />
      </Modal>
    </div>
  );
}

function BikeCard({ bike, spares }: { bike: BikeOut; spares: TireOut[] }) {
  const setDefault = useSetDefaultBike();
  const clearDefault = useClearDefaultBike();
  const retire = useRetireBike();

  return (
    <article className="gear-shoe gear-bike">
      <div className="gear-shoe__header">
        <span className="icon-chip">
          <Icon name="bike" />
        </span>
        <div>
          <h3>
            {bike.brand} {bike.model}
          </h3>
          <div className="gear-shoe__badges">
            {bike.retired && <span className="badge">Retired</span>}
            {bike.year && <span className="badge">{bike.year}</span>}
            {bike.size && <span className="badge">Size {bike.size}</span>}
            {bike.default_categories.map((c) => (
              <span className="badge" key={c}>
                {bikeCategoryLabel(c)}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="gear-shoe__mileage">
        <strong>{bike.distance_km.toFixed(0)} km</strong>
      </div>
      {bike.comments && <p className="gear-shoe__comments">{bike.comments}</p>}

      {!bike.retired && (
        <>
          <fieldset className="gear-form__sports">
            <legend>Default for</legend>
            <div className="gear-sport-options">
              {BIKE_CATEGORIES.map(({ key, label }) => {
                const checked = bike.default_categories.includes(key);
                return (
                  <label
                    key={key}
                    className={
                      checked
                        ? "gear-sport-option gear-sport-option--bike is-selected"
                        : "gear-sport-option gear-sport-option--bike"
                    }
                  >
                    <input
                      type="checkbox"
                      aria-label={`${bike.brand} ${bike.model} is the default for ${label}`}
                      checked={checked}
                      disabled={setDefault.isPending || clearDefault.isPending}
                      onChange={(e) =>
                        e.target.checked
                          ? setDefault.mutate({ category: key, bikeId: bike.id })
                          : clearDefault.mutate(key)
                      }
                    />
                    <span>{label}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <div className="gear-tires">
            {POSITIONS.map((position) => (
              <TireSlot
                key={position}
                bike={bike}
                position={position}
                tire={position === "front" ? bike.front_tire : bike.rear_tire}
                spares={spares}
              />
            ))}
          </div>

          <button
            type="button"
            className="button"
            disabled={retire.isPending}
            onClick={() => retire.mutate(bike.id)}
          >
            Retire bike
          </button>
        </>
      )}
    </article>
  );
}

const NEW_BIKE = {
  brand: "",
  model: "",
  year: "",
  size: "",
  comments: "",
  initialDistance: "0",
  defaults: [] as BikeCategory[],
};

function AddBikeForm({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState(NEW_BIKE);
  const create = useCreateBike();
  const setDefault = useSetDefaultBike();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const defaults = form.defaults;
    create.mutate(
      {
        brand: form.brand,
        model: form.model,
        year: form.year ? Number(form.year) : null,
        size: form.size || null,
        comments: form.comments || null,
        initial_distance_km: Number(form.initialDistance),
      },
      {
        onSuccess: (bike) => {
          defaults.forEach((category) => setDefault.mutate({ category, bikeId: bike.id }));
          setForm(NEW_BIKE);
          onDone();
        },
      },
    );
  };

  return (
    <Modal open onClose={onDone} title="Add a bike">
      <p className="gear-card__description">
        Initial distance includes kilometres already ridden before adding the bike. Tires are added
        to the bike afterwards, and a bike without tires is fine.
      </p>
      <form className="gear-form" onSubmit={submit}>
        <label className="field">
          Bike brand
          <input
            className="input"
            required
            value={form.brand}
            onChange={(e) => setForm({ ...form, brand: e.target.value })}
          />
        </label>
        <label className="field">
          Bike model
          <input
            className="input"
            required
            value={form.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })}
          />
        </label>
        <label className="field gear-form__size">
          Bike year
          <input
            className="input"
            type="number"
            min="1900"
            max="2100"
            step="1"
            value={form.year}
            onChange={(e) => setForm({ ...form, year: e.target.value })}
          />
        </label>
        <label className="field gear-form__size">
          Bike size
          <input
            className="input"
            value={form.size}
            onChange={(e) => setForm({ ...form, size: e.target.value })}
          />
        </label>
        <label className="field">
          Bike initial distance (km)
          <input
            className="input"
            type="number"
            min="0"
            step="1"
            required
            value={form.initialDistance}
            onChange={(e) => setForm({ ...form, initialDistance: e.target.value })}
          />
        </label>
        <fieldset className="gear-form__sports">
          <legend>Use as default for</legend>
          <div className="gear-sport-options">
            {BIKE_CATEGORIES.map(({ key, label }) => (
              <label
                key={key}
                className={
                  form.defaults.includes(key)
                    ? "gear-sport-option gear-sport-option--bike is-selected"
                    : "gear-sport-option gear-sport-option--bike"
                }
              >
                <input
                  type="checkbox"
                  checked={form.defaults.includes(key)}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      defaults: e.target.checked
                        ? [...form.defaults, key]
                        : form.defaults.filter((c) => c !== key),
                    })
                  }
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <label className="field gear-form__comments">
          Bike comments
          <textarea
            className="input"
            value={form.comments}
            onChange={(e) => setForm({ ...form, comments: e.target.value })}
          />
        </label>
        <div className="gear-form__actions">
          <button className="button button--primary" disabled={create.isPending}>
            <Icon name="bike" />
            {create.isPending ? "Adding…" : "Add bike"}
          </button>{" "}
          <button type="button" className="button" onClick={onDone}>
            Cancel
          </button>
        </div>
        {create.isError && (
          <p className="form-error" role="alert">
            Could not add this bike.
          </p>
        )}
      </form>
    </Modal>
  );
}

export function BikesSection() {
  const [showRetired, setShowRetired] = useState(false);
  const [addingSpare, setAddingSpare] = useState(false);
  const [addingBike, setAddingBike] = useState(false);
  const bikes = useBikes(showRetired);
  const tires = useTires();
  const install = useInstallTire();
  const retireTire = useRetireTire();
  const spares = (tires.data ?? []).filter((t) => t.bike_id == null && !t.retired);
  const activeBikes = (bikes.data ?? []).filter((b) => !b.retired);

  return (
    <>
      <section className="gear-inventory">
        <div className="gear-inventory__heading">
          <h2>Your bikes</h2>
          <button type="button" className="button" onClick={() => setAddingBike(true)}>
            + Add a bike
          </button>
          <label className="gear-show-retired">
            <input
              type="checkbox"
              checked={showRetired}
              onChange={(e) => setShowRetired(e.target.checked)}
            />
            Show retired
          </label>
        </div>
        {bikes.isLoading ? (
          <p className="gear-empty">Loading bikes…</p>
        ) : bikes.data?.length ? (
          <div className="gear-list">
            {bikes.data.map((bike) => (
              <BikeCard key={bike.id} bike={bike} spares={spares} />
            ))}
          </div>
        ) : (
          <div className="card gear-empty">
            <span className="icon-chip icon-chip--lg">
              <Icon name="bike" />
            </span>
            <h3>No bikes added yet</h3>
            <p>Use “Add a bike” above to track its distance and its tires.</p>
          </div>
        )}
      </section>

      <section className="gear-inventory">
        <h2>Spare tires</h2>
        <p className="gear-card__description">
          Tires that are not on a bike keep their distance and can be fitted again.
        </p>
        {spares.length > 0 && (
          <div className="gear-list">
            {spares.map((tire) => (
              <TireCard
                key={tire.id}
                tire={tire}
                actions={
                  <>
                    {activeBikes.length > 0 && (
                      <select
                        className="input"
                        aria-label={`Fit ${tire.brand} ${tire.model} to a bike`}
                        value=""
                        onChange={(e) => {
                          const [bikeId, position] = e.target.value.split("|");
                          if (bikeId && position)
                            install.mutate({
                              tireId: tire.id,
                              bikeId,
                              position: position as TirePosition,
                            });
                        }}
                        disabled={install.isPending}
                      >
                        <option value="">Fit to…</option>
                        {activeBikes.flatMap((b) =>
                          POSITIONS.map((p) => (
                            <option key={`${b.id}|${p}`} value={`${b.id}|${p}`}>
                              {b.brand} {b.model}, {p}
                            </option>
                          )),
                        )}
                      </select>
                    )}
                    <button
                      type="button"
                      className="button"
                      disabled={retireTire.isPending}
                      onClick={() => retireTire.mutate(tire.id)}
                    >
                      Retire tire
                    </button>
                  </>
                }
              />
            ))}
          </div>
        )}
        <button type="button" className="button" onClick={() => setAddingSpare(true)}>
          + Add a spare tire
        </button>
        <Modal open={addingSpare} onClose={() => setAddingSpare(false)} title="Add a spare tire">
          <TireForm onDone={() => setAddingSpare(false)} />
        </Modal>
      </section>

      {addingBike && <AddBikeForm onDone={() => setAddingBike(false)} />}
    </>
  );
}
