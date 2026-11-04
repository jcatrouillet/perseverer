// Settings: who coaches the athlete, and whom the athlete coaches (GET/PUT/DELETE /coach). The
// athlete picks a coach by username; the coach then sees their data and can plan workouts and
// comment, until the athlete stops sharing.
import { useState } from "react";

import { ApiError } from "../api/client";
import { useCoach, useSetCoach, useStopCoaching, useStopSharing } from "../api/queries";
import { useActingAs } from "../useActingAs";
import "../styles/settings.css";
import "../styles/coach.css";

function errorText(e: unknown): string {
  return e instanceof ApiError && e.detail ? e.detail : "Could not save.";
}

export function CoachCard() {
  const coach = useCoach();
  const setCoach = useSetCoach();
  const stopSharing = useStopSharing();
  const stopCoaching = useStopCoaching();
  const { viewAs } = useActingAs();
  const [username, setUsername] = useState("");

  if (coach.isError) {
    return (
      <section className="card">
        <h2>Coach</h2>
        <p role="alert" className="settings-form__error">
          Could not load your coach settings. If the server was just updated, restart it and reload.
        </p>
      </section>
    );
  }
  if (!coach.data) return null;
  const { coach: current, coachees } = coach.data;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const name = username.trim();
    if (!name) return;
    setCoach.mutate(name, { onSuccess: () => setUsername("") });
  };

  return (
    <section className="card">
      <h2>Coach</h2>
      <p className="chart-note">
        Choose a coach to share your whole view with. They can look through every screen as you,
        plan workouts and write comments on your activities; they cannot change your settings or
        accounts. You can stop sharing at any time.
      </p>

      {current ? (
        <p className="coach-current">
          Shared with <strong>{current.display_name}</strong>
          {current.username ? ` (${current.username})` : ""}.{" "}
          <button
            type="button"
            className="button"
            disabled={stopSharing.isPending}
            onClick={() => stopSharing.mutate()}
          >
            Stop sharing
          </button>
        </p>
      ) : (
        <p className="chart-note">You have no coach.</p>
      )}

      <form className="settings-form" onSubmit={submit}>
        <label>
          {current ? "Change coach" : "Coach username"}
          <input
            className="input coach-username"
            value={username}
            autoComplete="off"
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <div className="settings-form__actions">
          <button
            className="button button--primary"
            type="submit"
            disabled={setCoach.isPending || !username.trim()}
          >
            {current ? "Change coach" : "Share with this coach"}
          </button>
        </div>
        {setCoach.isError && (
          <span role="alert" className="settings-form__error">
            {errorText(setCoach.error)}
          </span>
        )}
      </form>

      {coachees.length > 0 && (
        <>
          <h3>Athletes you coach</h3>
          <ul className="coach-list">
            {coachees.map((c) => (
              <li key={c.id} className="coach-list__row">
                <span>{c.display_name}</span>
                <button
                  type="button"
                  className="button button--primary"
                  onClick={() => viewAs({ id: c.id, display_name: c.display_name })}
                >
                  View as {c.display_name}
                </button>
                <button
                  type="button"
                  className="button"
                  disabled={stopCoaching.isPending}
                  onClick={() => stopCoaching.mutate(c.id)}
                >
                  Stop coaching
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
