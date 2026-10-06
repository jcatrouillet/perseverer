// Settings page: opt in to the weekly / monthly training-report emails. See email_reports.py's
// own module docstring. Both switches are off by default. The SMTP relay is configured on the
// server (PERSEVERER_SMTP_*, not here); reports go to the athlete's own Profile email.
import { useEffect, useState } from "react";

import {
  useEmailReportConfig,
  useSendTestEmailReport,
  useSetEmailReportConfig,
} from "../api/queries";
import "../styles/settings.css";

function testErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("(400)")) return "Set your Profile email and configure SMTP first.";
  if (message.includes("(502)"))
    return "The server couldn't send the email — check the SMTP settings.";
  return "Could not send the test email.";
}

export function EmailReportsCard() {
  const config = useEmailReportConfig();
  const save = useSetEmailReportConfig();
  const test = useSendTestEmailReport();

  const [weekly, setWeekly] = useState(false);
  const [monthly, setMonthly] = useState(false);

  useEffect(() => {
    if (!config.data) return;
    setWeekly(config.data.weekly_enabled);
    setMonthly(config.data.monthly_enabled);
  }, [config.isSuccess, config.data]);

  const smtpConfigured = config.data?.smtp_configured ?? false;
  const recipient = config.data?.recipient_email ?? null;
  const disabled = config.isLoading || !smtpConfigured;

  return (
    <section className="card">
      <h2>Email reports</h2>
      <p className="chart-note">
        A weekly summary (Sunday evening) of the week's training plus the coming week's planned
        workouts, and/or a monthly summary on the last day of the month. Both are off by default.
      </p>

      {!smtpConfigured && (
        <p className="chart-note" role="status">
          Email delivery isn't configured on this server yet — ask the administrator to set the
          <code> PERSEVERER_SMTP_*</code> values.
        </p>
      )}
      {smtpConfigured && (
        <p className="chart-note">
          {recipient
            ? `Reports are sent to ${recipient}.`
            : "No email address set — add one on the Profile tab above first."}
        </p>
      )}

      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate({ weekly_enabled: weekly, monthly_enabled: monthly });
        }}
      >
        <label className="settings-form__checkbox">
          <input
            type="checkbox"
            checked={weekly}
            disabled={disabled}
            onChange={(e) => setWeekly(e.target.checked)}
          />
          Weekly summary — last week's stats and the coming week's planned workouts
        </label>
        <label className="settings-form__checkbox">
          <input
            type="checkbox"
            checked={monthly}
            disabled={disabled}
            onChange={(e) => setMonthly(e.target.checked)}
          />
          Monthly summary — last month's stats
        </label>

        <div className="settings-form__actions">
          <button
            className="button button--primary"
            type="submit"
            disabled={disabled || save.isPending}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
          <button
            className="button"
            type="button"
            disabled={!smtpConfigured || !recipient || test.isPending}
            onClick={() => test.mutate()}
          >
            {test.isPending ? "Sending…" : "Send test email"}
          </button>
        </div>

        {save.isError && (
          <span role="alert" className="settings-form__error">
            Could not save.
          </span>
        )}
        {save.isSuccess && <span className="settings-form__saved">Saved.</span>}
        {test.isError && (
          <span role="alert" className="settings-form__error">
            {testErrorMessage(test.error)}
          </span>
        )}
        {test.isSuccess && <span className="settings-form__saved">Test email sent.</span>}
      </form>
    </section>
  );
}
