// The athlete's own display preferences (GET /settings/personalize), available anywhere in the
// tree via `usePersonalize()` instead of prop-drilling through the many view components that
// each need one or more of these four settings (WeekView, MonthView, DateNavigator, ActivityCard,
// StatTile-driven distance displays, ...). `DEFAULTS` matches this app's own stated defaults
// (Monday, 24h, Week, metric) and is returned synchronously before the query resolves, so no
// consumer needs its own loading-state branch just to read a display preference.
import { createContext, useContext } from "react";

import { usePersonalizeSettings } from "./api/queries";
import type { PersonalizeSettingsOut } from "./api/types";

const DEFAULT_PERSONALIZE_SETTINGS: PersonalizeSettingsOut = {
  week_start_day: "monday",
  time_format: "24h",
  default_view: "week",
  unit_preference: "metric",
};

// Exported (not just the Provider/hook below) so a test can wrap a component in
// `<PersonalizeContext.Provider value={{...}}>` directly, without mocking the network layer
// just to exercise a non-default preference.
export const PersonalizeContext = createContext<PersonalizeSettingsOut>(
  DEFAULT_PERSONALIZE_SETTINGS,
);

export function PersonalizeProvider({ children }: { children: React.ReactNode }) {
  const query = usePersonalizeSettings();
  const value = query.data ?? DEFAULT_PERSONALIZE_SETTINGS;
  return <PersonalizeContext.Provider value={value}>{children}</PersonalizeContext.Provider>;
}

export function usePersonalize(): PersonalizeSettingsOut {
  return useContext(PersonalizeContext);
}
