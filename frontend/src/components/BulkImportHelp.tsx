// "How do I get this file?" for the Settings bulk import: the step-by-step way to request each
// vendor's full-account archive, in a popup so the card itself stays short. The steps follow
// Strava's own help article and Garmin's account Data Management page (as described by Garmin
// support and guides citing it); vendors rename menus now and then, so the steps name the pages
// rather than relying on exact positions.
import { useState } from "react";

import { Modal } from "./Modal";

export type BulkImportSource = "garmin" | "strava";

function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

function GarminSteps() {
  return (
    <>
      <ol className="bulk-import-help__steps">
        <li>
          Open Garmin's{" "}
          <ExternalLink href="https://www.garmin.com/account/datamanagement/">
            Account Data Management
          </ExternalLink>{" "}
          page and sign in with your Garmin account.
        </li>
        <li>
          Choose <strong>Export Your Data</strong>, then <strong>Request Data Export</strong>.
        </li>
        <li>
          Wait for Garmin's email. It usually arrives within 48 hours, but can take up to 30 days
          for a large account.
        </li>
        <li>
          Click <strong>Download</strong> in the email and save the .zip file.{" "}
          <strong>Don't unzip it</strong>: Perseverer finds the activity and health files inside,
          including the archives nested in it.
        </li>
        <li>
          Back here, choose <strong>Garmin export</strong> as the source, pick the .zip and click{" "}
          <strong>Upload and import</strong>.
        </li>
      </ol>
      <p className="chart-note">
        The archive holds your whole history: your activities' original files and your daily health
        data (sleep, heart rate, steps, HRV...). New activities keep arriving through the Garmin
        Connect sync; the archive is for the past.
      </p>
    </>
  );
}

function StravaSteps() {
  return (
    <>
      <ol className="bulk-import-help__steps">
        <li>
          On the Strava website (the app can't do this), sign in, hover over your name at the top
          right and choose <strong>Settings</strong>.
        </li>
        <li>
          Open <ExternalLink href="https://www.strava.com/account">My Account</ExternalLink> in the
          left-hand menu.
        </li>
        <li>
          Under <strong>Download your account</strong>, click <strong>Get Started</strong>, then{" "}
          <strong>Request download</strong>.
        </li>
        <li>
          Wait for Strava's email with the download link (usually a few hours). It goes to the email
          address on your Strava profile.
        </li>
        <li>
          Download the .zip file and, without unzipping it, upload it here with{" "}
          <strong>Strava export</strong> as the source.
        </li>
      </ol>
      <p className="chart-note">
        Perseverer imports the activities in it (their original FIT, GPX or TCX files, with Strava's
        activity list for names and totals). An activity also recorded by your Garmin is recognised
        and merged, not duplicated.
      </p>
    </>
  );
}

export function BulkImportHelp({ source }: { source: BulkImportSource }) {
  const [open, setOpen] = useState(false);
  const title = source === "garmin" ? "Get your Garmin export" : "Get your Strava export";
  return (
    <>
      <button type="button" className="bulk-import-help__link" onClick={() => setOpen(true)}>
        How do I get this file?
      </button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={title}
        panelClassName="modal__panel--text"
      >
        {source === "garmin" ? <GarminSteps /> : <StravaSteps />}
      </Modal>
    </>
  );
}
