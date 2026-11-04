// The athlete a coach is viewing as, as React state: re-renders on every switch (client.ts fires
// ACTING_AS_EVENT) and also when another tab changes it.
import { useCallback, useMemo, useSyncExternalStore } from "react";

import { ACTING_AS_EVENT, getActingAs, setActingAs, type ActingAs } from "./api/client";

function subscribe(onChange: () => void): () => void {
  window.addEventListener(ACTING_AS_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(ACTING_AS_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

// A string snapshot, so useSyncExternalStore sees an unchanged value as unchanged.
function snapshot(): string {
  const acting = getActingAs();
  return acting ? JSON.stringify(acting) : "";
}

export function useActingAs(): { actingAs: ActingAs | null; viewAs: (v: ActingAs | null) => void } {
  const raw = useSyncExternalStore(subscribe, snapshot, () => "");
  const actingAs = useMemo(() => (raw ? (JSON.parse(raw) as ActingAs) : null), [raw]);
  const viewAs = useCallback((value: ActingAs | null) => setActingAs(value), []);
  return { actingAs, viewAs };
}
