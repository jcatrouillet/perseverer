// A plain, explicit sign-out action. Clears the stored credential via client.ts's own
// clearCredential() -- the same call the "background 401 clears credential" path already uses,
// which AuthGate listens for (AUTH_CLEARED_EVENT) to fall back to the login screen. Unlike that
// existing path, this one also clears the query cache: this app is multi-tenant (a second
// athlete is a real, exercised setup -- see CLAUDE.md), so two athletes plausibly share a
// browser, and a fresh login after an explicit logout should never briefly show the previous
// athlete's already-cached data before its own queries load in.
import { useQueryClient } from "@tanstack/react-query";

import { clearCredential } from "../api/client";

export function LogoutButton() {
  const queryClient = useQueryClient();

  function handleLogout() {
    queryClient.clear();
    clearCredential();
  }

  return (
    <button type="button" className="button" onClick={handleLogout}>
      Log out
    </button>
  );
}
