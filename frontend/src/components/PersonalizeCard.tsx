// Settings page: display preferences (GET/PUT /settings/personalize) -- pure presentation,
// never read by any backend computation, unlike Profile's own fields. Read app-wide via
// PersonalizeContext.tsx's usePersonalize() hook once saved here.
import { useEffect, useState } from "react";

import { usePersonalizeSettings, useSetPersonalizeSettings } from "../api/queries";
import type { DefaultView, TimeFormat, UnitPreference, WeekStartDay } from "../api/types";
import { getEffectiveTheme, setStoredTheme, type Theme } from "../theme";
import "../styles/settings.css";

export function PersonalizeCard() {
  const settings = usePersonalizeSettings();
  const save = useSetPersonalizeSettings();

  const [weekStartDay, setWeekStartDay] = useState<WeekStartDay>("monday");
  const [timeFormat, setTimeFormat] = useState<TimeFormat>("24h");
  const [defaultView, setDefaultView] = useState<DefaultView>("week");
  const [unitPreference, setUnitPreference] = useState<UnitPreference>("metric");
  // Unlike every field below, the color theme is a per-device browser setting (localStorage, see
  // theme.ts) rather than an account setting, so it applies the moment it is picked and is not
  // part of the Save button's payload.
  const [theme, setTheme] = useState<Theme>(getEffectiveTheme);

  const chooseTheme = (next: Theme) => {
    setStoredTheme(next);
    setTheme(next);
  };

  useEffect(() => {
    if (!settings.data) return;
    setWeekStartDay(settings.data.week_start_day);
    setTimeFormat(settings.data.time_format);
    setDefaultView(settings.data.default_view);
    setUnitPreference(settings.data.unit_preference);
  }, [settings.isSuccess, settings.data]);

  return (
    <section className="card">
      <h2>Personalize</h2>
      <p className="chart-note">
        Display preferences only -- these change how dates, times, and distances are shown, never
        what's actually stored or how any training metric is computed.
      </p>

      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate({
            week_start_day: weekStartDay,
            time_format: timeFormat,
            default_view: defaultView,
            unit_preference: unitPreference,
          });
        }}
      >
        <fieldset className="settings-form__row">
          <legend>Color theme</legend>
          <label>
            <input
              type="radio"
              name="color_theme"
              checked={theme === "light"}
              onChange={() => chooseTheme("light")}
            />
            Light
          </label>
          <label>
            <input
              type="radio"
              name="color_theme"
              checked={theme === "dark"}
              onChange={() => chooseTheme("dark")}
            />
            Dark
          </label>
          <span className="chart-note">Applies immediately, on this device only.</span>
        </fieldset>

        <fieldset className="settings-form__row">
          <legend>Week starts on</legend>
          <label>
            <input
              type="radio"
              name="week_start_day"
              checked={weekStartDay === "monday"}
              onChange={() => setWeekStartDay("monday")}
            />
            Monday
          </label>
          <label>
            <input
              type="radio"
              name="week_start_day"
              checked={weekStartDay === "sunday"}
              onChange={() => setWeekStartDay("sunday")}
            />
            Sunday
          </label>
        </fieldset>

        <fieldset className="settings-form__row">
          <legend>Time format</legend>
          <label>
            <input
              type="radio"
              name="time_format"
              checked={timeFormat === "24h"}
              onChange={() => setTimeFormat("24h")}
            />
            24-hour
          </label>
          <label>
            <input
              type="radio"
              name="time_format"
              checked={timeFormat === "12h"}
              onChange={() => setTimeFormat("12h")}
            />
            12-hour (AM/PM)
          </label>
        </fieldset>

        <fieldset className="settings-form__row">
          <legend>Distance units</legend>
          <label>
            <input
              type="radio"
              name="unit_preference"
              checked={unitPreference === "metric"}
              onChange={() => setUnitPreference("metric")}
            />
            Kilometers
          </label>
          <label>
            <input
              type="radio"
              name="unit_preference"
              checked={unitPreference === "imperial"}
              onChange={() => setUnitPreference("imperial")}
            />
            Miles
          </label>
        </fieldset>

        <label className="field settings-form__inline">
          <span>Starting page</span>
          <select
            className="input"
            value={defaultView}
            onChange={(e) => setDefaultView(e.target.value as DefaultView)}
          >
            <option value="week">Week view</option>
            <option value="month">Month view</option>
            <option value="day">Day view</option>
            <option value="activities">Activities view</option>
          </select>
        </label>

        <div className="settings-form__actions">
          <button className="button button--primary" type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </div>

        {save.isError && (
          <span role="alert" className="settings-form__error">
            Could not save.
          </span>
        )}
        {save.isSuccess && <span className="settings-form__saved">Saved.</span>}
      </form>
    </section>
  );
}
