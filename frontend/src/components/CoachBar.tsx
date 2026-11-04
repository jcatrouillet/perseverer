// The coach's way into an athlete's view and back: a "View as" switcher in the nav (only for
// someone who coaches somebody) and, while viewing as an athlete, a banner that says so and
// returns to the coach's own view. Switching re-keys the app (App.tsx), so every screen reloads
// the other athlete's data.
import { useQueryClient } from "@tanstack/react-query";

import { useCoach } from "../api/queries";
import { useActingAs } from "../useActingAs";
import "../styles/coach.css";

const OWN_VIEW = "";

export function CoacheeSwitcher() {
  const coach = useCoach();
  const { actingAs, viewAs } = useActingAs();
  const queryClient = useQueryClient();
  const listed = coach.data?.coachees ?? [];
  // Until the list loads, the athlete being viewed must still be a choice, or the select would
  // read "My view" while the banner says otherwise.
  const coachees =
    actingAs && !listed.some((c) => c.id === actingAs.id)
      ? [...listed, { ...actingAs, username: null, since: "" }]
      : listed;
  if (coachees.length === 0) return null;

  const change = (id: string) => {
    queryClient.clear();
    const chosen = coachees.find((c) => c.id === id);
    viewAs(chosen ? { id: chosen.id, display_name: chosen.display_name } : null);
  };

  return (
    <label className="coach-switcher">
      <span className="coach-switcher__label">View as</span>
      <select
        className="input coach-switcher__select"
        value={actingAs?.id ?? OWN_VIEW}
        onChange={(e) => change(e.target.value)}
      >
        <option value={OWN_VIEW}>My view</option>
        {coachees.map((c) => (
          <option key={c.id} value={c.id}>
            {c.display_name}
          </option>
        ))}
      </select>
    </label>
  );
}

export function CoachBanner() {
  const { actingAs, viewAs } = useActingAs();
  const queryClient = useQueryClient();
  if (!actingAs) return null;
  return (
    <div className="coach-banner" role="status">
      <span>
        Viewing <strong>{actingAs.display_name}</strong> as their coach. You can plan workouts and
        write comments; everything else is read-only.
      </span>
      <button
        type="button"
        className="button"
        onClick={() => {
          queryClient.clear();
          viewAs(null);
        }}
      >
        Back to my view
      </button>
    </div>
  );
}
