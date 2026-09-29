export type Theme = "dark" | "light";

const STORAGE_KEY = "perseverer.theme";

export function getStoredTheme(): Theme | null {
  const value = localStorage.getItem(STORAGE_KEY);
  return value === "dark" || value === "light" ? value : null;
}

/** The theme actually showing: the stored choice, else the OS's own light/dark setting. */
export function getEffectiveTheme(): Theme {
  return (
    getStoredTheme() ??
    (window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark")
  );
}

export function setStoredTheme(theme: Theme): void {
  localStorage.setItem(STORAGE_KEY, theme);
  document.documentElement.dataset.theme = theme;
}

// Applied once at startup (before React renders) so there's no flash of the wrong theme while
// the app boots. A stored choice always wins; otherwise theme.css's own
// prefers-color-scheme fallback handles an unset preference.
export function applyStoredTheme(): void {
  const stored = getStoredTheme();
  if (stored) {
    document.documentElement.dataset.theme = stored;
  }
}
