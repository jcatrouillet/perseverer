import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { SplitOut } from "../api/types";
import { BoulderingRoutesTable } from "./BoulderingRoutesTable";

function split(overrides: Partial<SplitOut> = {}): SplitOut {
  return {
    split_index: 0,
    split_type: "climb_active",
    start_time_utc: "2026-08-19T01:46:47Z",
    end_time_utc: "2026-08-19T01:47:38Z",
    duration_s: 51.6,
    distance_m: null,
    climb_grade: 2,
    climb_result: "completed",
    climb_avg_hr: 99,
    climb_max_hr: 132,
    is_manual: null,
    ...overrides,
  };
}

const noop = () => {};

describe("BoulderingRoutesTable", () => {
  it("still renders the add-route row when there are no routes logged yet", () => {
    render(
      <BoulderingRoutesTable
        splits={[]}
        onSetStatus={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    expect(screen.getByText("Add route")).toBeInTheDocument();
    expect(screen.queryByText(/routes completed/)).not.toBeInTheDocument();
  });

  it("lists one row per route, in order, with grade/status/HR", () => {
    render(
      <BoulderingRoutesTable
        splits={[
          split({ split_index: 0, climb_grade: 2, climb_result: "completed" }),
          split({ split_index: 1, split_type: "climb_rest", climb_grade: null, climb_result: null }),
          split({ split_index: 2, climb_grade: 4, climb_result: "attempt", climb_avg_hr: 150 }),
        ]}
        onSetStatus={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]!).getByText("V2")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("V4")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("150 bpm")).toBeInTheDocument();
    // Status is an editable <select>, not plain text -- confirm via its current value instead.
    expect(within(rows[1]!).getByRole("combobox")).toHaveValue("completed");
    expect(within(rows[2]!).getByRole("combobox")).toHaveValue("attempt");
  });

  it("captions with the completed/total count", () => {
    render(
      <BoulderingRoutesTable
        splits={[
          split({ split_index: 0, climb_result: "completed" }),
          split({ split_index: 2, climb_result: "attempt" }),
          split({ split_index: 4, climb_result: "attempt" }),
        ]}
        onSetStatus={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    expect(screen.getByText("1 of 3 routes completed.")).toBeInTheDocument();
  });

  it("shows a dash rather than a fabricated value when HR is missing", () => {
    render(
      <BoulderingRoutesTable
        splits={[split({ climb_avg_hr: null })]}
        onSetStatus={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    const rows = screen.getAllByRole("row");
    expect(rows[1]!).toHaveTextContent("—");
  });

  it("calls onSetStatus with the route's own split_index when its status is changed", () => {
    const onSetStatus = vi.fn();
    render(
      <BoulderingRoutesTable
        splits={[split({ split_index: 5, climb_result: "attempt" })]}
        onSetStatus={onSetStatus}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    const select = screen.getAllByRole("combobox")[0]!;
    fireEvent.change(select, { target: { value: "completed" } });
    expect(onSetStatus).toHaveBeenCalledWith(5, "completed");
  });

  it("shows a delete button only for a manually-added route", () => {
    render(
      <BoulderingRoutesTable
        splits={[
          split({ split_index: 0, is_manual: null }),
          split({ split_index: 1, is_manual: true }),
        ]}
        onSetStatus={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    expect(screen.getAllByRole("button", { name: "Remove this route" })).toHaveLength(1);
  });

  it("calls onDeleteRoute with the manual route's split_index", () => {
    const onDeleteRoute = vi.fn();
    render(
      <BoulderingRoutesTable
        splits={[split({ split_index: 3, is_manual: true })]}
        onSetStatus={noop}
        onAddRoute={noop}
        onDeleteRoute={onDeleteRoute}
        isSaving={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove this route" }));
    expect(onDeleteRoute).toHaveBeenCalledWith(3);
  });

  it("calls onAddRoute with the selected grade and result", () => {
    const onAddRoute = vi.fn();
    render(
      <BoulderingRoutesTable
        splits={[]}
        onSetStatus={noop}
        onAddRoute={onAddRoute}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    // Defaults to V0/Completed -- confirm the default submission without touching the selects.
    fireEvent.click(screen.getByText("Add route"));
    expect(onAddRoute).toHaveBeenCalledWith(0, "completed");
  });

  it("shows an error message when isError is true", () => {
    render(
      <BoulderingRoutesTable
        splits={[]}
        onSetStatus={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={true}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save");
  });
});
