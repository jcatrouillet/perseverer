import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActivityMergePreviewOut, DuplicateCandidateOut } from "../api/types";
import { ActivityMergePanel } from "./ActivityMergePanel";

function candidate(overrides: Partial<DuplicateCandidateOut> = {}): DuplicateCandidateOut {
  return {
    id: "other1",
    name: "Half Dome",
    primary_source: "strava_export",
    start_time_utc: "2022-06-07T12:48:32Z",
    distance_m: 26863.1,
    duration_s: 41913.0,
    ...overrides,
  };
}

function preview(): ActivityMergePreviewOut {
  return {
    fields: [
      { field: "distance_m", self_value: 29445.7, other_value: 26863.1 },
      { field: "calories", self_value: null, other_value: 378.0 },
      { field: "route", self_value: "self", other_value: "other" },
    ],
  };
}

const noop = () => {};

describe("ActivityMergePanel", () => {
  it("renders nothing when there are no candidates", () => {
    const { container } = render(
      <ActivityMergePanel
        candidates={[]}
        expandedCandidateId={null}
        onExpandCandidate={noop}
        preview={undefined}
        isPreviewLoading={false}
        onMerge={noop}
        isMerging={false}
        mergeError={false}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the candidate banner and a Review merge button, collapsed by default", () => {
    render(
      <ActivityMergePanel
        candidates={[candidate()]}
        expandedCandidateId={null}
        onExpandCandidate={noop}
        preview={undefined}
        isPreviewLoading={false}
        onMerge={noop}
        isMerging={false}
        mergeError={false}
      />,
    );
    expect(screen.getByText(/This might be the same as/)).toBeInTheDocument();
    expect(screen.getByText("Review merge")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("calls onExpandCandidate with the candidate id when Review merge is clicked", () => {
    const onExpandCandidate = vi.fn();
    render(
      <ActivityMergePanel
        candidates={[candidate()]}
        expandedCandidateId={null}
        onExpandCandidate={onExpandCandidate}
        preview={undefined}
        isPreviewLoading={false}
        onMerge={noop}
        isMerging={false}
        mergeError={false}
      />,
    );
    fireEvent.click(screen.getByText("Review merge"));
    expect(onExpandCandidate).toHaveBeenCalledWith("other1");
  });

  it("renders one comparison row per field, defaulting to self", () => {
    render(
      <ActivityMergePanel
        candidates={[candidate()]}
        expandedCandidateId="other1"
        onExpandCandidate={noop}
        preview={preview()}
        isPreviewLoading={false}
        onMerge={noop}
        isMerging={false}
        mergeError={false}
      />,
    );
    const rows = screen.getAllByRole("row");
    // header + 3 field rows
    expect(rows).toHaveLength(4);
    const distanceRow = rows.find((r) => within(r).queryByText("Distance"));
    expect(distanceRow).toBeDefined();
    const radios = within(distanceRow!).getAllByRole("radio");
    expect(radios[0]).toBeChecked(); // "self" is the default
    expect(radios[1]).not.toBeChecked();
  });

  it("labels and formats a max_altitude_m field comparison in metres", () => {
    render(
      <ActivityMergePanel
        candidates={[candidate()]}
        expandedCandidateId="other1"
        onExpandCandidate={noop}
        preview={{
          fields: [{ field: "max_altitude_m", self_value: 2690.3, other_value: 2685.0 }],
        }}
        isPreviewLoading={false}
        onMerge={noop}
        isMerging={false}
        mergeError={false}
      />,
    );
    expect(screen.getByText("Max elevation")).toBeInTheDocument();
    expect(screen.getByText("2690 m")).toBeInTheDocument();
    expect(screen.getByText("2685 m")).toBeInTheDocument();
  });

  it("calls onMerge with the chosen fields when Confirm merge is clicked", () => {
    const onMerge = vi.fn();
    render(
      <ActivityMergePanel
        candidates={[candidate()]}
        expandedCandidateId="other1"
        onExpandCandidate={noop}
        preview={preview()}
        isPreviewLoading={false}
        onMerge={onMerge}
        isMerging={false}
        mergeError={false}
      />,
    );
    const rows = screen.getAllByRole("row");
    const caloriesRow = rows.find((r) => within(r).queryByText("Calories"));
    const otherRadio = within(caloriesRow!).getAllByRole("radio")[1]!;
    fireEvent.click(otherRadio);

    fireEvent.click(screen.getByText("Confirm merge"));
    expect(onMerge).toHaveBeenCalledWith("other1", { calories: "other" });
  });

  it("calls onExpandCandidate(null) when Cancel is clicked", () => {
    const onExpandCandidate = vi.fn();
    render(
      <ActivityMergePanel
        candidates={[candidate()]}
        expandedCandidateId="other1"
        onExpandCandidate={onExpandCandidate}
        preview={preview()}
        isPreviewLoading={false}
        onMerge={noop}
        isMerging={false}
        mergeError={false}
      />,
    );
    fireEvent.click(screen.getByText("Cancel"));
    expect(onExpandCandidate).toHaveBeenCalledWith(null);
  });

  it("shows an error message when mergeError is true", () => {
    render(
      <ActivityMergePanel
        candidates={[candidate()]}
        expandedCandidateId="other1"
        onExpandCandidate={noop}
        preview={preview()}
        isPreviewLoading={false}
        onMerge={noop}
        isMerging={false}
        mergeError={true}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/Could not merge/);
  });

  it("shows a loading state while the preview is fetching", () => {
    render(
      <ActivityMergePanel
        candidates={[candidate()]}
        expandedCandidateId="other1"
        onExpandCandidate={noop}
        preview={undefined}
        isPreviewLoading={true}
        onMerge={noop}
        isMerging={false}
        mergeError={false}
      />,
    );
    expect(screen.getByText("Loading comparison…")).toBeInTheDocument();
  });
});
