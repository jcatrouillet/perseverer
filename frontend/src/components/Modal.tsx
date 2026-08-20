// A generic full-screen popup, built from scratch since this codebase's existing
// "reveal more" patterns (ActivityNameCorrection, ActivitySportCorrection) are all deliberately
// small inline forms, not modals -- the Goals progress graph needs the opposite: a big popup
// that isn't visible inline on the page at all (see GoalButton.tsx). Portaled to document.body
// so it isn't clipped by any ancestor's overflow/stacking context, closes on Escape or a
// backdrop click, and traps neither focus nor scroll beyond what the backdrop itself needs
// (a deliberately minimal implementation -- this app has exactly one modal use case so far).
import { useEffect } from "react";
import { createPortal } from "react-dom";

import "../styles/modal.css";

export function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="modal__backdrop" onClick={onClose}>
      <div
        className="modal__panel"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal__header">
          <h2>{title}</h2>
          <button type="button" className="modal__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="modal__body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
