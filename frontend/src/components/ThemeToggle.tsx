import { useState } from "react";

import { getEffectiveTheme, setStoredTheme, type Theme } from "../theme";

// Only rendered on the sign-in screen now (AuthGate), where Settings is unreachable -- once signed
// in, the theme choice lives in Settings > Personalize.

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(getEffectiveTheme);

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setStoredTheme(next);
    setTheme(next);
  };

  return (
    <button type="button" className="button" onClick={toggle} aria-label="Toggle color theme">
      {theme === "dark" ? "Light mode" : "Dark mode"}
    </button>
  );
}
