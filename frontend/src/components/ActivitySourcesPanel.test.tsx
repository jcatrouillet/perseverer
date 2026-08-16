import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActivitySourcesOut } from "../api/types";
import { ActivitySourcesPanel } from "./ActivitySourcesPanel";

function sources(overrides: Partial<ActivitySourcesOut> = {}): ActivitySourcesOut {
  return {
    sources: [
      { link_id: 1, source: "fit_folder", external_id: "abc", ingested_at: "2025-06-01T00:00:00Z", can_split: true },
      { link_id: 2, source: "strava_export", external_id: "999", ingested_at: "2026-04-18T00:00:00Z", can_split: true },
    ],
    merge_decisions: [
      { candidate_ref: "2026-04-17T22:17:09", reasons: ["start_time delta 0s <= 180s", "sport family match"], decided_at: "2026-04-18T00:00:00Z" },
    ],
    ...overrides,
  };
}

describe("ActivitySourcesPanel", () => {
  it("renders nothing with no sources", () => {
    const { container } = render(
      <ActivitySourcesPanel
        sources={sources({ sources: [] })}
        onSplit={vi.fn()}
        isSplitting={false}
        splitError={false}
        splitSuccess={false}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("lists each source with a human label", () => {
    render(
      <ActivitySourcesPanel
        sources={sources()}
        onSplit={vi.fn()}
        isSplitting={false}
        splitError={false}
        splitSuccess={false}
      />,
    );
    expect(screen.getByText("FIT file")).toBeInTheDocument();
    expect(screen.getByText("Strava export")).toBeInTheDocument();
  });

  it("does not show a split action for the sole source", () => {
    render(
      <ActivitySourcesPanel
        sources={sources({
          sources: [
            { link_id: 1, source: "fit_folder", external_id: "abc", ingested_at: "2025-06-01T00:00:00Z", can_split: false },
          ],
        })}
        onSplit={vi.fn()}
        isSplitting={false}
        splitError={false}
        splitSuccess={false}
      />,
    );
    expect(screen.queryByText("Not the same activity?")).not.toBeInTheDocument();
  });

  it("requires an explicit confirm before calling onSplit", () => {
    const onSplit = vi.fn();
    render(
      <ActivitySourcesPanel
        sources={sources()}
        onSplit={onSplit}
        isSplitting={false}
        splitError={false}
        splitSuccess={false}
      />,
    );
    const triggers = screen.getAllByText("Not the same activity?");
    fireEvent.click(triggers[0]);
    expect(onSplit).not.toHaveBeenCalled();
    expect(screen.getByText("Split this source into its own activity?")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Confirm"));
    expect(onSplit).toHaveBeenCalledWith(1);
  });

  it("cancel dismisses the confirm step without calling onSplit", () => {
    const onSplit = vi.fn();
    render(
      <ActivitySourcesPanel
        sources={sources()}
        onSplit={onSplit}
        isSplitting={false}
        splitError={false}
        splitSuccess={false}
      />,
    );
    fireEvent.click(screen.getAllByText("Not the same activity?")[0]);
    fireEvent.click(screen.getByText("Cancel"));
    expect(onSplit).not.toHaveBeenCalled();
    expect(screen.queryByText("Split this source into its own activity?")).not.toBeInTheDocument();
  });

  it("shows the merge-decision reasons behind a details toggle", () => {
    render(
      <ActivitySourcesPanel
        sources={sources()}
        onSplit={vi.fn()}
        isSplitting={false}
        splitError={false}
        splitSuccess={false}
      />,
    );
    expect(screen.getByText("Why these were merged")).toBeInTheDocument();
    expect(screen.getByText(/sport family match/)).toBeInTheDocument();
  });

  it("shows an error message when the split failed", () => {
    render(
      <ActivitySourcesPanel
        sources={sources()}
        onSplit={vi.fn()}
        isSplitting={false}
        splitError={true}
        splitSuccess={false}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not split");
  });
});
