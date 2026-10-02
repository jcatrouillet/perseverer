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
        onSetGrade={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    expect(screen.getByText("Add route")).toBeInTheDocument();
    expect(screen.queryByText(/routes completed/)).not.toBeInTheDocument();
  });

  it("shows a Kaya route's name and an ungraded route as V?", () => {
    render(
      <BoulderingRoutesTable
        splits={[
          split({ split_index: 0, climb_grade: 2, climb_name: "Pink - A8 - Alcove, Right" }),
          split({
            split_index: 1,
            climb_grade: null,
            climb_result: "attempt",
            climb_name: "Blue - A1 - Intro Area",
          }),
          split({ split_index: 2, climb_grade: 3, climb_name: null }),
        ]}
        onSetStatus={noop}
        onSetGrade={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    expect(screen.getByText("Pink - A8 - Alcove, Right")).toBeInTheDocument();
    const ungraded = screen.getByText("Blue - A1 - Intro Area").closest("tr")!;
    expect(within(ungraded).getByRole("option", { name: "V?" })).toBeInTheDocument();
    expect(screen.getByText("2 of 3 routes completed.")).toBeInTheDocument();
  });

  it("hides the per-route Duration and Avg HR columns when the routes come from Kaya", () => {
    render(
      <BoulderingRoutesTable
        splits={[
          split({ split_index: 0, climb_name: "Pink - A8", source: "kaya", duration_s: null }),
          split({ split_index: 1, climb_name: "Blue - A1", source: "kaya", duration_s: null }),
        ]}
        onSetStatus={noop}
        onSetGrade={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    expect(screen.queryByRole("columnheader", { name: "Duration" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Avg HR" })).not.toBeInTheDocument();
    expect(screen.getByText("Pink - A8")).toBeInTheDocument();
    expect(screen.getByText("Add route")).toBeInTheDocument();
  });

  it("keeps the timing columns when Garmin efforts sit alongside Kaya routes", () => {
    render(
      <BoulderingRoutesTable
        splits={[
          split({ split_index: 0, climb_name: "Pink - A8", source: "kaya", duration_s: null }),
          split({ split_index: 1, climb_grade: 4, source: "garmin_extra", duration_s: 80 }),
        ]}
        onSetStatus={noop}
        onSetGrade={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    expect(screen.getByRole("columnheader", { name: "Duration" })).toBeInTheDocument();
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
        onSetGrade={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    const rows = screen.getAllByRole("row");
    expect(within(rows[2]!).getByText("150 bpm")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Duration" })).toBeInTheDocument();
    // Grade and status are both editable <select>s, not plain text -- confirm via their current
    // values instead. Grade is the first combobox in the row, status the second.
    const row1Selects = within(rows[1]!).getAllByRole("combobox");
    const row2Selects = within(rows[2]!).getAllByRole("combobox");
    expect(row1Selects[0]).toHaveValue("2");
    expect(row1Selects[1]).toHaveValue("completed");
    expect(row2Selects[0]).toHaveValue("4");
    expect(row2Selects[1]).toHaveValue("attempt");
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
        onSetGrade={noop}
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
        onSetGrade={noop}
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
        onSetGrade={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    // Grade is the first combobox in the row, status the second.
    const select = screen.getAllByRole("combobox")[1]!;
    fireEvent.change(select, { target: { value: "completed" } });
    expect(onSetStatus).toHaveBeenCalledWith(5, "completed");
  });

  it("calls onSetGrade with the route's own split_index when its grade is changed", () => {
    const onSetGrade = vi.fn();
    render(
      <BoulderingRoutesTable
        splits={[split({ split_index: 5, climb_grade: 2 })]}
        onSetStatus={noop}
        onSetGrade={onSetGrade}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={false}
      />,
    );
    const select = screen.getAllByRole("combobox")[0]!;
    fireEvent.change(select, { target: { value: "6" } });
    expect(onSetGrade).toHaveBeenCalledWith(5, 6);
  });

  it("shows a delete button only for a manually-added route", () => {
    render(
      <BoulderingRoutesTable
        splits={[
          split({ split_index: 0, is_manual: null }),
          split({ split_index: 1, is_manual: true }),
        ]}
        onSetStatus={noop}
        onSetGrade={noop}
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
        onSetGrade={noop}
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
        onSetGrade={noop}
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
        onSetGrade={noop}
        onAddRoute={noop}
        onDeleteRoute={noop}
        isSaving={false}
        isError={true}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save");
  });
});
