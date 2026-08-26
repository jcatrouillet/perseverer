// Wraps a chart's own title so it opens that same chart larger in a Modal -- mobile only (see
// useIsMobile.ts), since every chart in this app is a Recharts <ResponsiveContainer> or a
// hand-rolled SVG that already reads fine at its normal inline size on a desktop-width screen;
// mobile is where a chart that has to fit a phone-width card genuinely loses readable detail.
//
// Reuses the existing Modal (built for the Goals progress graph) rather than the map's own
// CSS-class fullscreen toggle (ActivityRoute.tsx) -- deliberately: that pattern re-styles one
// specific, already-isolated container, but charts live nested inside many different cards
// across the app, several with their own `overflow: hidden` for rounded corners, which would
// clip a CSS-fixed-position element unless it happened to escape every ancestor's stacking
// context. Modal already solves exactly this via `createPortal(..., document.body)` -- no
// per-chart escape-the-ancestors work needed no matter how deeply nested a given chart is.
//
// `children` is passed to both the always-rendered inline spot and the Modal -- cheap when the
// modal is closed (Modal returns null immediately, so the modal copy is constructed as a React
// element but never actually mounted/rendered into the DOM) and gives a real second, independent,
// larger instance of the same chart only once actually opened.
import { useState } from "react";

import { Icon } from "./Icon";
import { Modal } from "./Modal";

export function ChartFullscreen({
  title,
  as: Heading = "h3",
  className,
  /** Rendered before the title text in both the mobile button and the desktop static span --
      e.g. ActivityCharts.tsx's per-panel icon-chip, which every panel heading has today and
      the fullscreen affordance shouldn't drop just because the title became clickable. Not
      included in the Modal's own header (that already gets a plain string title). */
  titlePrefix,
  children,
}: {
  title: string;
  as?: "h2" | "h3" | "h4";
  className?: string;
  titlePrefix?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  // A chart that renders nothing (no data for this range) shouldn't leave its own heading and
  // fullscreen affordance dangling above an empty spot -- a summary view should only show what
  // it actually has, section and all, not just the chart body. Every caller already renders
  // its chart unconditionally as `children`, so checking here covers all of them at once.
  if (children == null) {
    return null;
  }

  return (
    <>
      <Heading
        className={className ? `${className} chart-fullscreen-heading` : "chart-fullscreen-heading"}
      >
        <button type="button" className="chart-fullscreen-trigger" onClick={() => setOpen(true)}>
          {titlePrefix}
          {title}
          <Icon name="expand" className="chart-fullscreen-icon" />
        </button>
        <span className="chart-fullscreen-static-title" aria-hidden="true">
          {titlePrefix}
          {title}
        </span>
      </Heading>
      {children}
      <Modal open={open} onClose={() => setOpen(false)} title={title}>
        <div className="chart-fullscreen-modal-body">{children}</div>
      </Modal>
    </>
  );
}
