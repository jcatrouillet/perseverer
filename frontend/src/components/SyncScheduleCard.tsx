// Settings: when each connected source (Garmin Connect, Eufy scale, Kaya) syncs by itself
// (GET/PUT /settings/sync-schedule). Times are on the athlete's own clock (their profile time
// zone); a source left on its default follows the deployment's time. The worker checks every few
// minutes, so a source starts within `window_minutes` after its time.
import { useEffect, useState } from "react";

import { useSetSyncSchedule, useSyncSchedule } from "../api/queries";
import type { SyncSourceOut } from "../api/types";
import { usePersonalize } from "../PersonalizeContext";
import "../styles/settings.css";

interface Draft {
  enabled: boolean;
  time: string;
  /** Changed by the athlete: until then a default time is saved as "still the default". */
  edited: boolean;
}

/** "Sat 04:15" / "Sat 4:15 AM" on `timeZone`'s clock, from the UTC instant the API sends. */
export function formatNextRun(iso: string, timeZone: string, hour12: boolean): string {
  return new Date(iso).toLocaleString("en-US", {
    weekday: "short",
    hour: hour12 ? "numeric" : "2-digit",
    minute: "2-digit",
    hour12,
    timeZone,
  });
}

export function SyncScheduleCard() {
  const schedule = useSyncSchedule();
  const save = useSetSyncSchedule();
  const hour12 = usePersonalize().time_format === "12h";
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});

  useEffect(() => {
    if (!schedule.data) return;
    setDrafts(
      Object.fromEntries(
        schedule.data.sources.map((s) => [
          s.source,
          { enabled: s.enabled, time: s.time, edited: false },
        ]),
      ),
    );
  }, [schedule.data]);

  if (!schedule.data) return null;
  const { sources, window_minutes: windowMinutes } = schedule.data;

  const change = (source: string, patch: Partial<Draft>) =>
    setDrafts((d) => ({ ...d, [source]: { ...d[source]!, ...patch, edited: true } }));

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    save.mutate({
      sources: sources.map((s: SyncSourceOut) => {
        const d = drafts[s.source] ?? { enabled: s.enabled, time: s.time, edited: false };
        return {
          source: s.source,
          // An untouched default stays the default (it follows the deployment's time).
          time: s.is_default && !d.edited ? null : d.time,
          enabled: d.enabled,
        };
      }),
    });
  };

  return (
    <section className="card">
      <h2>Automatic sync times</h2>
      <p className="chart-note">
        Each connected source syncs by itself once a day, at the time you choose here, on your own
        clock ({sources[0]?.timezone}; change it under Profile). A sync starts within{" "}
        {windowMinutes} minutes after its time. A source you have not connected never runs.
      </p>
      <form className="settings-form" onSubmit={submit}>
        <ul className="settings-sync__list">
          {sources.map((s) => {
            const d = drafts[s.source];
            if (!d) return null;
            return (
              <li key={s.source} className="settings-sync__row">
                <label className="settings-sync__enabled">
                  <input
                    type="checkbox"
                    checked={d.enabled}
                    disabled={!s.configured}
                    onChange={(e) => change(s.source, { enabled: e.target.checked })}
                  />
                  {s.label}
                </label>
                <input
                  className="input settings-sync__time"
                  type="time"
                  aria-label={`${s.label} sync time`}
                  value={d.time}
                  disabled={!s.configured || !d.enabled}
                  onChange={(e) => e.target.value && change(s.source, { time: e.target.value })}
                />
                <span className="chart-note settings-sync__meta">
                  {!s.configured
                    ? "Not connected"
                    : !d.enabled
                      ? "Off"
                      : s.next_run_at
                        ? `Next: ${formatNextRun(s.next_run_at, s.timezone, hour12)}${
                            s.is_default && !d.edited ? " (default time)" : ""
                          }`
                        : ""}
                </span>
              </li>
            );
          })}
        </ul>
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
