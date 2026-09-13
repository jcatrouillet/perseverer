// An athlete's own configured HR training zones -- see hr_zones.py's own docstring for the
// blended-formula rationale (heart rate reserve for zones 1-2, %threshold for zones 3-4).
// Setting these here is what switches TimeInZoneChart (activity detail) over from the device's
// own per-activity zone breakdown to zones computed from the raw HR stream against these three
// reference values -- see ActivityDetailPage.tsx's own wiring.
import { useEffect, useState } from "react";
import { Link } from "wouter";

import {
  useAthleteProfile,
  useChangePassword,
  useDuplicatePairs,
  useHealthDashboard,
  useHrZoneConfig,
  usePerformance,
  useRunningLoadConfig,
  useSetAthleteProfile,
  useSetHrZoneConfig,
  useSetRunningLoadConfig,
  useTrimCandidates,
} from "../api/queries";
import type { DuplicateCandidateOut, TrimCandidateOut } from "../api/types";
import { hrZoneRangeLabel } from "../activityMetrics";
import { isoDate } from "../dateUtils";
import { formatDurationHM, formatMinPerKm } from "../runningStats";
import { ApiKeyCard } from "../components/ApiKeyCard";
import { BulkImportCard } from "../components/BulkImportCard";
import { CalendarFeedCard } from "../components/CalendarFeedCard";
import { EmailReportsCard } from "../components/EmailReportsCard";
import { EufyCard } from "../components/EufyCard";
import { GarminConnectCard } from "../components/GarminConnectCard";
import { LoadingSpinner } from "../components/LoadingSpinner";
import { MetricExplorer, type ExplorerMetric } from "../components/MetricExplorer";
import { RebuildCard } from "../components/RebuildCard";
import "../styles/settings.css";

// How far back to look for a recent computed/observed value to suggest in the HR-zones/
// threshold-pace forms below -- only the most recent value matters here, not a chart's full
// history, so this stays a small, cheap query rather than reusing EARLIEST_PLAUSIBLE_DATE.
const SUGGESTION_WINDOW_DAYS = 30;

const SOURCE_LABELS: Record<string, string> = {
  fit_folder: "FIT file",
  garmin_export: "Garmin export",
  garmin_connect: "Garmin Connect",
  strava_export: "Strava export",
};

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatDistanceKm(distanceM: number | null): string {
  return distanceM == null ? "—" : `${(distanceM / 1000).toFixed(2)} km`;
}

export function TrimCandidateRow({ candidate }: { candidate: TrimCandidateOut }) {
  const boundaries = [candidate.flag.at_start && "start", candidate.flag.at_end && "end"].filter(
    Boolean,
  );
  return (
    <li className="settings-scan__row">
      <Link href={`/activities/${candidate.id}`} className="settings-scan__link">
        {candidate.name ?? "Untitled activity"}
      </Link>
      <span className="settings-scan__meta">
        {formatDate(candidate.start_time_utc)} · {candidate.sport} ·{" "}
        {formatDistanceKm(candidate.distance_m)}
        {candidate.duration_s != null && ` · ${formatDurationHM(candidate.duration_s)}`}
      </span>
      <span className="settings-scan__flag">
        Fast segment at the {boundaries.join(" and ")} of the recording
      </span>
    </li>
  );
}

export function DuplicateSideLink({ activity }: { activity: DuplicateCandidateOut }) {
  return (
    <Link href={`/activities/${activity.id}`} className="settings-scan__link">
      {activity.name ?? "Untitled activity"} ({sourceLabel(activity.primary_source)})
    </Link>
  );
}

function toInputValue(bpm: number | null): string {
  return bpm == null ? "" : String(bpm);
}

// "m:ss" pace text <-> seconds/km, for the running threshold-pace field below. No existing
// parser to reuse (runningStats.ts only ever formats a pace for display, never parses one back).
function parsePaceInput(value: string): number | null {
  const match = /^(\d+):([0-5]\d)$/.exec(value.trim());
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function formatPaceInput(secPerKm: number | null): string {
  return secPerKm == null ? "" : formatMinPerKm(secPerKm / 60);
}

function parseField(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

// Most recent row with a non-null value for `pick`, scanning from the end -- usePerformance
// returns ascending by local_date, and a suggestion only ever wants the latest one, not a chart's
// full series. Avoids Array.prototype.findLast (ES2023, past this project's ES2022 lib target).
function latestNonNull<T>(rows: T[] | undefined, pick: (row: T) => number | null): number | null {
  if (!rows) return null;
  for (let i = rows.length - 1; i >= 0; i--) {
    const value = pick(rows[i]);
    if (value != null) return value;
  }
  return null;
}

export function SettingsPage() {
  // Which of the four sections below is showing -- the page was one long scroll of nine cards
  // before this, which the user found too long; MetricExplorer (already used by Fitness & Form/
  // Health for the exact same "pick one thing from a list, show only that" shape) gives this a
  // left-hand section list with only the selected section's cards rendered, for free.
  const [selectedSection, setSelectedSection] = useState<string | null>(null);

  const config = useHrZoneConfig();
  const mutation = useSetHrZoneConfig();
  const runningLoadConfig = useRunningLoadConfig();
  const runningLoadMutation = useSetRunningLoadConfig();
  const profileConfig = useAthleteProfile();
  const profileMutation = useSetAthleteProfile();
  const passwordMutation = useChangePassword();
  const trimCandidates = useTrimCandidates();
  const duplicatePairs = useDuplicatePairs();

  const today = isoDate(new Date());
  const suggestionWindowStart = isoDate(
    new Date(Date.now() - SUGGESTION_WINDOW_DAYS * 24 * 60 * 60 * 1000),
  );
  const performance = usePerformance(suggestionWindowStart, today);
  const healthDashboard = useHealthDashboard(suggestionWindowStart, today);

  const [maxHr, setMaxHr] = useState("");
  const [thresholdHr, setThresholdHr] = useState("");
  const [restingHr, setRestingHr] = useState("");
  const [thresholdPaceText, setThresholdPaceText] = useState("");
  const [birthdate, setBirthdate] = useState("");
  const [heightCm, setHeightCm] = useState("");
  const [sex, setSex] = useState<"" | "male" | "female">("");
  const [email, setEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirm, setNewPasswordConfirm] = useState("");

  // Same once-on-arrival pre-fill as the HR zone/running-load config above.
  useEffect(() => {
    if (!profileConfig.data) return;
    setBirthdate(profileConfig.data.birthdate ?? "");
    setHeightCm(toInputValue(profileConfig.data.height_cm));
    setSex(profileConfig.data.sex ?? "");
    setEmail(profileConfig.data.email ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileConfig.isSuccess]);

  const heightCmNum = parseField(heightCm);
  const profileClientError =
    heightCm.trim() !== "" && heightCmNum == null
      ? "Height must be a number."
      : heightCmNum != null && (heightCmNum < 50 || heightCmNum > 250)
        ? "Height must be between 50 and 250 cm."
        : birthdate !== "" && new Date(birthdate) > new Date()
          ? "Birthdate can't be in the future."
          : email.trim() !== "" && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())
            ? "Enter a valid email address."
            : null;

  const passwordClientError =
    newPassword !== "" && newPassword.length < 8
      ? "New password must be at least 8 characters."
      : newPassword !== "" && newPassword !== newPasswordConfirm
        ? "New password and confirmation don't match."
        : null;

  function handlePasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (passwordClientError || newPassword === "") return;
    passwordMutation.mutate(
      { current_password: currentPassword, new_password: newPassword },
      {
        onSettled: () => {
          // Never lingers in state, success or failure -- same precedent as GarminConnectCard's
          // own password field.
          setCurrentPassword("");
          setNewPassword("");
          setNewPasswordConfirm("");
        },
      },
    );
  }

  // Same once-on-arrival pre-fill as the HR zone config below.
  useEffect(() => {
    if (!runningLoadConfig.data) return;
    setThresholdPaceText(formatPaceInput(runningLoadConfig.data.threshold_pace_sec_per_km));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningLoadConfig.isSuccess]);

  const thresholdPaceSecPerKm = parsePaceInput(thresholdPaceText);
  const paceClientError =
    thresholdPaceText.trim() !== "" && thresholdPaceSecPerKm == null
      ? "Enter a pace as m:ss, e.g. 5:08."
      : null;

  const suggestedThresholdPace = latestNonNull(
    performance.data,
    (row) => row.threshold_pace_s_per_km,
  );

  // Pre-fill from the loaded config -- once, when it first arrives, not on every refetch (an
  // in-progress edit shouldn't be clobbered by a background refetch of the same query).
  useEffect(() => {
    if (!config.data) return;
    setMaxHr(toInputValue(config.data.max_hr_bpm));
    setThresholdHr(toInputValue(config.data.threshold_hr_bpm));
    setRestingHr(toInputValue(config.data.resting_hr_bpm));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.isSuccess]);

  const maxHrNum = parseField(maxHr);
  const thresholdHrNum = parseField(thresholdHr);
  const restingHrNum = parseField(restingHr);

  const clientError =
    maxHrNum != null && restingHrNum != null && maxHrNum <= restingHrNum
      ? "Max HR must be greater than resting HR."
      : maxHrNum != null && thresholdHrNum != null && thresholdHrNum > maxHrNum
        ? "Threshold HR can't exceed max HR."
        : null;

  // Max/threshold HR come from this app's own computed performance rollup (empirical, or the
  // Tanaka/88%-of-max formula fallbacks -- see performance_rollup.py); resting HR has no computed
  // source anywhere in this app, so it's suggested from Garmin's own observed daily reading
  // instead (GET /health/dashboard's resting_heart_rate logical metric).
  const suggestedMaxHr = latestNonNull(performance.data, (row) => row.max_hr_bpm);
  const suggestedThresholdHr = latestNonNull(performance.data, (row) => row.threshold_hr_bpm);
  const restingHrMetric = healthDashboard.data?.metrics.find(
    (m) => m.logical_metric === "resting_heart_rate",
  );
  const suggestedRestingHr = restingHrMetric?.daily.at(-1)?.value_last ?? null;
  const hasHrSuggestion =
    suggestedMaxHr != null || suggestedThresholdHr != null || suggestedRestingHr != null;
  // Shown alongside the button itself, not just on it -- the user should see what a click would
  // actually fill in before committing to it, not just an opaque "Use suggested values" label.
  const hrSuggestionLabel = [
    suggestedMaxHr != null && `Max HR ${Math.round(suggestedMaxHr)} bpm`,
    suggestedThresholdHr != null && `Threshold HR ${Math.round(suggestedThresholdHr)} bpm`,
    suggestedRestingHr != null && `Resting HR ${Math.round(suggestedRestingHr)} bpm`,
  ]
    .filter(Boolean)
    .join(" · ");

  function applySuggestedHrValues() {
    if (suggestedMaxHr != null) setMaxHr(toInputValue(Math.round(suggestedMaxHr)));
    if (suggestedThresholdHr != null) {
      setThresholdHr(toInputValue(Math.round(suggestedThresholdHr)));
    }
    if (suggestedRestingHr != null) setRestingHr(toInputValue(Math.round(suggestedRestingHr)));
  }

  const preview = mutation.data ?? (config.data?.max_hr_bpm != null ? config.data : null);
  const previewZones =
    preview?.zone1_high_bpm != null &&
    preview.zone2_high_bpm != null &&
    preview.zone3_high_bpm != null &&
    preview.zone4_high_bpm != null
      ? [
          { index: 1, seconds: 0, lowBoundary: null, highBoundary: preview.zone1_high_bpm },
          {
            index: 2,
            seconds: 0,
            lowBoundary: preview.zone1_high_bpm,
            highBoundary: preview.zone2_high_bpm,
          },
          {
            index: 3,
            seconds: 0,
            lowBoundary: preview.zone2_high_bpm,
            highBoundary: preview.zone3_high_bpm,
          },
          {
            index: 4,
            seconds: 0,
            lowBoundary: preview.zone3_high_bpm,
            highBoundary: preview.zone4_high_bpm,
          },
          { index: 5, seconds: 0, lowBoundary: preview.zone4_high_bpm, highBoundary: null },
        ]
      : [];

  const sections: ExplorerMetric[] = [
    {
      key: "profile",
      title: "Profile",
      content: (
        <>
          <section className="card">
            <h2>Profile</h2>
            <p className="chart-note">
              Birthdate/height/sex are optional -- only used to fill in max HR and BMR (calorie)
              estimates with a formula when there isn't enough of your own real data yet; your own
              observed max HR and any Eufy-scale readings always take priority once they exist.
              Email is where the weekly/monthly summaries go, if you enable them under External
              tools.
            </p>
            <form
              className="settings-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (profileClientError) return;
                profileMutation.mutate({
                  birthdate: birthdate === "" ? null : birthdate,
                  height_cm: heightCmNum,
                  sex: sex === "" ? null : sex,
                  email: email.trim() === "" ? null : email.trim(),
                });
              }}
            >
              <label>
                Birthdate
                <input
                  type="date"
                  value={birthdate}
                  onChange={(e) => setBirthdate(e.target.value)}
                />
              </label>
              <label>
                Height (cm)
                <input
                  type="number"
                  inputMode="numeric"
                  value={heightCm}
                  onChange={(e) => setHeightCm(e.target.value)}
                  placeholder="e.g. 178"
                />
              </label>
              <label>
                Biological sex
                <select
                  value={sex}
                  onChange={(e) => setSex(e.target.value as "" | "male" | "female")}
                >
                  <option value="">Not set</option>
                  <option value="male">Male</option>
                  <option value="female">Female</option>
                </select>
              </label>
              <label>
                Email
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  autoComplete="off"
                />
              </label>
              <button
                type="submit"
                disabled={profileMutation.isPending || profileClientError != null}
              >
                {profileMutation.isPending ? "Saving…" : "Save"}
              </button>
              {profileClientError && (
                <span role="alert" className="settings-form__error">
                  {profileClientError}
                </span>
              )}
              {profileMutation.isError && !profileClientError && (
                <span role="alert" className="settings-form__error">
                  Could not save.
                </span>
              )}
              {profileMutation.isSuccess && <span className="settings-form__saved">Saved.</span>}
            </form>
          </section>

          <section className="card">
            <h2>Password</h2>
            <p className="chart-note">Change your own login password.</p>
            <form className="settings-form" onSubmit={handlePasswordSubmit}>
              <label>
                Current password
                <input
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  autoComplete="off"
                  required
                />
              </label>
              <label>
                New password
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  autoComplete="off"
                  required
                />
              </label>
              <label>
                Confirm new password
                <input
                  type="password"
                  value={newPasswordConfirm}
                  onChange={(e) => setNewPasswordConfirm(e.target.value)}
                  autoComplete="off"
                  required
                />
              </label>
              <button
                type="submit"
                disabled={passwordMutation.isPending || passwordClientError != null}
              >
                {passwordMutation.isPending ? "Saving…" : "Change password"}
              </button>
              {passwordClientError && (
                <span role="alert" className="settings-form__error">
                  {passwordClientError}
                </span>
              )}
              {passwordMutation.isError && !passwordClientError && (
                <span role="alert" className="settings-form__error">
                  Could not change password -- check your current password.
                </span>
              )}
              {passwordMutation.isSuccess && (
                <span className="settings-form__saved">Password changed.</span>
              )}
            </form>
          </section>

          <ApiKeyCard />
        </>
      ),
    },
    {
      key: "external-tools",
      title: "External tools",
      content: (
        <>
          <GarminConnectCard />
          <EufyCard />
          <CalendarFeedCard />
          <EmailReportsCard />
        </>
      ),
    },
    {
      key: "physical-profile",
      title: "Physical profile",
      content: (
        <>
          <section className="card">
            <h2>HR training zones</h2>
            <p className="chart-note">
              Five zones, derived from three reference values: zones 1-2 from heart rate reserve
              (Karvonen), zones 3-4 from lactate threshold HR. Leave blank to fall back to each
              activity's own device-reported zones.
            </p>
            {hasHrSuggestion && (
              <p className="settings-suggest">
                Suggested: {hrSuggestionLabel}{" "}
                <button type="button" className="button" onClick={applySuggestedHrValues}>
                  Use suggested values
                </button>
              </p>
            )}
            <form
              className="settings-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (clientError) return;
                mutation.mutate({
                  max_hr_bpm: maxHrNum,
                  threshold_hr_bpm: thresholdHrNum,
                  resting_hr_bpm: restingHrNum,
                });
              }}
            >
              <label>
                Max HR (bpm)
                <input
                  type="number"
                  inputMode="numeric"
                  value={maxHr}
                  onChange={(e) => setMaxHr(e.target.value)}
                  placeholder="e.g. 190"
                />
              </label>
              <label>
                Threshold HR (bpm)
                <input
                  type="number"
                  inputMode="numeric"
                  value={thresholdHr}
                  onChange={(e) => setThresholdHr(e.target.value)}
                  placeholder="e.g. 168"
                />
              </label>
              <label>
                Resting HR (bpm)
                <input
                  type="number"
                  inputMode="numeric"
                  value={restingHr}
                  onChange={(e) => setRestingHr(e.target.value)}
                  placeholder="e.g. 48"
                />
              </label>
              <button type="submit" disabled={mutation.isPending || clientError != null}>
                {mutation.isPending ? "Saving…" : "Save"}
              </button>
              {clientError && (
                <span role="alert" className="settings-form__error">
                  {clientError}
                </span>
              )}
              {mutation.isError && !clientError && (
                <span role="alert" className="settings-form__error">
                  Could not save -- check the values are sane (max &gt; resting, threshold ≤ max).
                </span>
              )}
              {mutation.isSuccess && <span className="settings-form__saved">Saved.</span>}
            </form>

            {previewZones.length > 0 && (
              <table className="settings-hr-zones__preview">
                <thead>
                  <tr>
                    <th>Zone</th>
                    <th>Range (bpm)</th>
                  </tr>
                </thead>
                <tbody>
                  {previewZones.map((zone) => (
                    <tr key={zone.index}>
                      <td>Z{zone.index}</td>
                      <td>{hrZoneRangeLabel(zone)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="card">
            <h2>Running training load</h2>
            <p className="chart-note">
              Your threshold pace -- roughly your best sustainable ~1-hour effort -- calibrates
              running's Fitness &amp; Form training load to the same 100-per-hour-at-threshold scale
              TrainingPeaks and intervals.icu use, in place of Garmin's own uncalibrated Training
              Load. Leave blank to keep using Garmin's number for running, same as every other
              sport.
            </p>
            {suggestedThresholdPace != null && (
              <p className="settings-suggest">
                Suggested: {formatPaceInput(suggestedThresholdPace)} /km{" "}
                <button
                  type="button"
                  className="button"
                  onClick={() => setThresholdPaceText(formatPaceInput(suggestedThresholdPace))}
                >
                  Use suggested value
                </button>
              </p>
            )}
            <form
              className="settings-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (paceClientError) return;
                runningLoadMutation.mutate({ threshold_pace_sec_per_km: thresholdPaceSecPerKm });
              }}
            >
              <label>
                Threshold pace (min/km)
                <input
                  type="text"
                  inputMode="numeric"
                  value={thresholdPaceText}
                  onChange={(e) => setThresholdPaceText(e.target.value)}
                  placeholder="e.g. 5:08"
                />
              </label>
              <button
                type="submit"
                disabled={runningLoadMutation.isPending || paceClientError != null}
              >
                {runningLoadMutation.isPending ? "Saving…" : "Save"}
              </button>
              {paceClientError && (
                <span role="alert" className="settings-form__error">
                  {paceClientError}
                </span>
              )}
              {runningLoadMutation.isError && !paceClientError && (
                <span role="alert" className="settings-form__error">
                  Could not save.
                </span>
              )}
              {runningLoadMutation.isSuccess && (
                <span className="settings-form__saved">Saved.</span>
              )}
            </form>
          </section>
        </>
      ),
    },
    {
      key: "activities",
      title: "Activities",
      content: (
        <>
          <RebuildCard />
          <BulkImportCard />

          <section className="card">
            <h2>Activities that may need trimming</h2>
            <p className="chart-note">
              Hiking/walking recordings with a sustained fast segment at the start or end -- usually
              a stretch of car travel the recording was never stopped for. Open one to review and
              trim it.
            </p>
            {trimCandidates.isLoading && <LoadingSpinner />}
            {trimCandidates.isError && <p role="alert">Could not load.</p>}
            {trimCandidates.data && trimCandidates.data.length === 0 && (
              <p className="settings-scan__empty">Nothing flagged.</p>
            )}
            {trimCandidates.data && trimCandidates.data.length > 0 && (
              <details className="settings-scan__details">
                <summary>{trimCandidates.data.length} flagged</summary>
                <ul className="settings-scan__list">
                  {trimCandidates.data.map((candidate) => (
                    <TrimCandidateRow key={candidate.id} candidate={candidate} />
                  ))}
                </ul>
              </details>
            )}
          </section>

          <section className="card">
            <h2>Possible duplicate activities</h2>
            <p className="chart-note">
              Activities recorded by more than one source (e.g. a Garmin device and Strava's own
              auto-detection) that were never merged into one record. Open either side to review and
              merge.
            </p>
            {duplicatePairs.isLoading && <LoadingSpinner />}
            {duplicatePairs.isError && <p role="alert">Could not load.</p>}
            {duplicatePairs.data && duplicatePairs.data.length === 0 && (
              <p className="settings-scan__empty">Nothing flagged.</p>
            )}
            {duplicatePairs.data && duplicatePairs.data.length > 0 && (
              <details className="settings-scan__details">
                <summary>{duplicatePairs.data.length} flagged</summary>
                <ul className="settings-scan__list">
                  {duplicatePairs.data.map((pair) => (
                    <li
                      key={`${pair.activity_a.id}-${pair.activity_b.id}`}
                      className="settings-scan__row"
                    >
                      <span>
                        <DuplicateSideLink activity={pair.activity_a} /> and{" "}
                        <DuplicateSideLink activity={pair.activity_b} />
                      </span>
                      <span className="settings-scan__meta">
                        {formatDate(pair.activity_a.start_time_utc)} ·{" "}
                        {formatDistanceKm(pair.activity_a.distance_m)} vs.{" "}
                        {formatDistanceKm(pair.activity_b.distance_m)}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </section>
        </>
      ),
    },
  ];

  return (
    <main>
      <h1>Settings</h1>
      <MetricExplorer metrics={sections} selected={selectedSection} onSelect={setSelectedSection} />
    </main>
  );
}
