// A share link that anyone can open without logging in -- see sharing.py's own docstring for
// what's deliberately left out of the public page (weight, HR). Two small exported components
// (activity vs. period) rather than one with a branching prop, since react-query hooks can't be
// called conditionally -- both share the same modal/copy-link rendering below.
import { useState } from "react";

import { useCreateActivityShare, useCreatePeriodShare } from "../api/queries";
import "../styles/share-button.css";
import { Modal } from "./Modal";

function ShareLinkModal({ url, onClose }: { url: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(url);
    setCopied(true);
  }

  return (
    <Modal open onClose={onClose} title="Share link">
      <p className="chart-note">
        Anyone with this link can view this — without signing in.
      </p>
      <div className="share-button__url-row">
        <input className="input" type="text" value={url} readOnly onFocus={(e) => e.target.select()} />
        <button className="button button--primary" type="button" onClick={() => void handleCopy()}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </Modal>
  );
}

export function ActivityShareButton({ activityId }: { activityId: string }) {
  const share = useCreateActivityShare(activityId);
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        className="button"
        type="button"
        disabled={share.isPending}
        onClick={() => share.mutate(undefined, { onSuccess: () => setOpen(true) })}
      >
        {share.isPending ? "Creating link…" : "Share"}
      </button>
      {share.isError && (
        <span role="alert" className="share-button__error">
          Could not create a share link.
        </span>
      )}
      {open && share.data && <ShareLinkModal url={share.data.url} onClose={() => setOpen(false)} />}
    </>
  );
}

export function PeriodShareButton({
  periodType,
  periodStart,
}: {
  periodType: "week" | "month" | "year" | "all";
  periodStart?: string;
}) {
  const share = useCreatePeriodShare(periodType, periodStart);
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        className="button"
        type="button"
        disabled={share.isPending}
        onClick={() => share.mutate(undefined, { onSuccess: () => setOpen(true) })}
      >
        {share.isPending ? "Creating link…" : "Share"}
      </button>
      {share.isError && (
        <span role="alert" className="share-button__error">
          Could not create a share link.
        </span>
      )}
      {open && share.data && <ShareLinkModal url={share.data.url} onClose={() => setOpen(false)} />}
    </>
  );
}
