import { useState } from "react";

import { getStoredTheme, setStoredTheme, type Theme } from "../theme";

function currentTheme(): Theme {
  return (
    getStoredTheme() ??
    (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
  );
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);

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
