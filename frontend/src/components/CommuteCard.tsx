// Settings: the optional home and work addresses that let the app spot commutes by itself
// (GET/PUT /settings/commute). The card explains why it asks, what it does with the addresses and
// where they go, since asking for an address with no explanation would rightly raise eyebrows.
import { FormEvent, useEffect, useState } from "react";

import { ApiError } from "../api/client";
import { useCommuteSettings, useSetCommuteSettings } from "../api/queries";
import "../styles/settings.css";

export function CommuteCard() {
  const settings = useCommuteSettings();
  const save = useSetCommuteSettings();
  const [home, setHome] = useState("");
  const [work, setWork] = useState("");

  useEffect(() => {
    if (!settings.data) return;
    setHome(settings.data.home_address ?? "");
    setWork(settings.data.work_address ?? "");
  }, [settings.data]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate({ home_address: home.trim() || null, work_address: work.trim() || null });
  };

  const data = settings.data;
  const error =
    save.error instanceof ApiError
      ? (save.error.detail ?? "Couldn't save the addresses.")
      : save.error
        ? "Couldn't save the addresses."
        : null;

  return (
    <section className="card">
      <h2>Commute</h2>
      <p className="chart-note">
        <strong>Optional.</strong> Give your home and work addresses and Perseverer will tag as a
        commute every activity that goes from one to the other, in either direction, so you
        don&apos;t have to mark them by hand. Commutes can then be left out of your Insights
        (Personalize). Leave both empty to skip it: nothing else uses them.
      </p>
      <ul className="chart-note settings-commute__facts">
        <li>
          An activity counts when its start and its finish are each within {data?.radius_m ?? 400} m
          of one of the two addresses. Both addresses are needed.
        </li>
        <li>
          A tag you set yourself on an activity always wins over the detection, and you can still
          tag or untag any activity on its page.
        </li>
        <li>
          The addresses stay on your own server. To find where an address is, its text is sent once
          to OpenStreetMap&apos;s Nominatim search service (the same one used to name where an
          activity took place), and only again if you change it.
        </li>
        <li>
          Separately, activities with &quot;Zwift&quot; in the title are tagged as virtual
          automatically.
        </li>
      </ul>

      <form className="settings-form" onSubmit={submit}>
        <div className="settings-commute__field">
          <label>
            Home address
            <input
              className="input"
              type="text"
              autoComplete="off"
              placeholder="Street, city, country"
              value={home}
              onChange={(e) => setHome(e.target.value)}
            />
          </label>
          {data?.home_label && data.home_address === home.trim() && (
            <span className="settings-commute__found">Found: {data.home_label}</span>
          )}
        </div>
        <div className="settings-commute__field">
          <label>
            Work address
            <input
              className="input"
              type="text"
              autoComplete="off"
              placeholder="Street, city, country"
              value={work}
              onChange={(e) => setWork(e.target.value)}
            />
          </label>
          {data?.work_label && data.work_address === work.trim() && (
            <span className="settings-commute__found">Found: {data.work_label}</span>
          )}
        </div>
        <button type="submit" className="button button--primary" disabled={save.isPending}>
          {save.isPending ? "Looking up…" : "Save"}
        </button>
        {error && <span role="alert">{error}</span>}
        {save.isSuccess && !error && <span className="settings-form__saved">Saved.</span>}
      </form>

      {data && (
        <p className="chart-note" aria-live="polite">
          {data.detection_active
            ? data.detected_count === 0
              ? "Commute detection is on. No activity matches yet."
              : `Commute detection is on: ${data.detected_count} ${
                  data.detected_count === 1 ? "activity is" : "activities are"
                } tagged as a commute from your addresses.`
            : "Commute detection is off until both addresses are set."}
        </p>
      )}
    </section>
  );
}
