import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BloodTestResultOut } from "../api/types";
import { BloodTestsPanel } from "./BloodTestsPanel";

const mockUseBloodTests = vi.fn();
const mockCreateBatchMutate = vi.fn();
const mockUpdateMutate = vi.fn();
const mockUpdateMutateAsync = vi.fn().mockResolvedValue(undefined);
const mockDeleteResultMutate = vi.fn();
const mockDeletePanelMutate = vi.fn();

vi.mock("../api/queries", () => ({
  useBloodTests: (...args: unknown[]) => mockUseBloodTests(...args),
  useCreateBloodTestBatch: () => ({
    mutate: mockCreateBatchMutate,
    isPending: false,
    isError: false,
  }),
  useUpdateBloodTestResult: () => ({
    mutate: mockUpdateMutate,
    mutateAsync: mockUpdateMutateAsync,
    isPending: false,
  }),
  useDeleteBloodTestResult: () => ({ mutate: mockDeleteResultMutate, isPending: false }),
  useDeleteBloodTestPanel: () => ({ mutate: mockDeletePanelMutate, isPending: false }),
}));

function result(overrides: Partial<BloodTestResultOut> = {}): BloodTestResultOut {
  return {
    id: 1,
    local_date: "2026-06-01",
    marker: "LDL Cholesterol",
    value_num: 110,
    unit: "mg/dL",
    reference_low: 0,
    reference_high: 130,
    lab_name: "Quest Diagnostics",
    notes: null,
    created_at: "2026-06-01T10:00:00Z",
    updated_at: "2026-06-01T10:00:00Z",
    ...overrides,
  };
}

const EMPTY = { data: undefined, isLoading: false, isError: false };

describe("BloodTestsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a loading spinner while fetching", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, isLoading: true });
    render(<BloodTestsPanel />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows an empty state when no blood tests are recorded", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [] });
    render(<BloodTestsPanel />);
    expect(screen.getByText("No blood tests recorded yet.")).toBeInTheDocument();
  });

  it("groups results by draw date, most recent first, showing the lab name and marker count", () => {
    mockUseBloodTests.mockReturnValue({
      ...EMPTY,
      data: [
        result({ id: 1, local_date: "2026-01-01", marker: "HDL", lab_name: "LabCorp" }),
        result({ id: 2, local_date: "2026-06-01", marker: "LDL", lab_name: "Quest" }),
        result({ id: 3, local_date: "2026-06-01", marker: "HbA1c", lab_name: "Quest" }),
      ],
    });
    render(<BloodTestsPanel />);

    const summaries = screen.getAllByText(/marker\(s\)/);
    expect(summaries[0]).toHaveTextContent("2 marker(s)");
    expect(summaries[1]).toHaveTextContent("1 marker(s)");
    expect(screen.getByText("Quest")).toBeInTheDocument();
    expect(screen.getByText("LabCorp")).toBeInTheDocument();
  });

  it("formats the draw date without shifting a day for a west-of-UTC local timezone", () => {
    // Regression: parseIsoDate returns a UTC-midnight Date, and toLocaleDateString silently
    // rolls that back to the previous calendar day in any timezone behind UTC unless
    // timeZone: "UTC" is passed explicitly -- confirmed live (entering "2026-09-13" rendered
    // back as "Sep 12, 2026" before this fix). Asserting the exact string here, not just that
    // *a* date renders, is what pins the fix down: the test would only pass regardless of the
    // machine's own local timezone (jsdom's default here) with the fix in place.
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [result({ local_date: "2026-09-13" })] });
    render(<BloodTestsPanel />);
    expect(screen.getByText("Sep 13, 2026")).toBeInTheDocument();
  });

  it("flags a value outside the athlete's own reference range", () => {
    mockUseBloodTests.mockReturnValue({
      ...EMPTY,
      data: [result({ value_num: 200, reference_high: 130 })],
    });
    render(<BloodTestsPanel />);
    expect(screen.getByText("Out of range")).toBeInTheDocument();
  });

  it("does not flag a value inside the athlete's own reference range", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [result({ value_num: 100 })] });
    render(<BloodTestsPanel />);
    expect(screen.queryByText("Out of range")).not.toBeInTheDocument();
  });

  it("hides the add form by default, showing only a '+ Add blood test' button", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [] });
    render(<BloodTestsPanel />);
    expect(screen.getByRole("button", { name: "+ Add blood test" })).toBeInTheDocument();
    expect(screen.queryByText("Save blood test")).not.toBeInTheDocument();
  });

  it("reveals the add form on click, and submits a batch with the entered markers", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [] });
    render(<BloodTestsPanel />);

    fireEvent.click(screen.getByRole("button", { name: "+ Add blood test" }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "__new_marker__" } });
    fireEvent.change(screen.getByPlaceholderText("e.g. LDL Cholesterol"), {
      target: { value: "Glucose" },
    });
    const numberInputs = screen.getAllByRole("spinbutton");
    fireEvent.change(numberInputs[0]!, { target: { value: "95" } });
    fireEvent.click(screen.getByRole("button", { name: "Save blood test" }));

    expect(mockCreateBatchMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        results: [
          expect.objectContaining({ marker: "Glucose", value_num: 95 }),
        ],
      }),
      expect.anything(),
    );
  });

  it("supports adding and removing marker rows in the add form", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [] });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add blood test" }));

    expect(screen.getAllByRole("combobox")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "+ Add another marker" }));
    expect(screen.getAllByRole("combobox")).toHaveLength(2);

    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]!);
    expect(screen.getAllByRole("combobox")).toHaveLength(1);
  });

  it("shows a marker dropdown, not free text, once the athlete has entered a marker before", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [result()] });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add blood test" }));

    expect(screen.queryByPlaceholderText("e.g. LDL Cholesterol")).not.toBeInTheDocument();
    const select = screen.getByRole("combobox");
    expect(select).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "LDL Cholesterol" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "+ New marker…" })).toBeInTheDocument();
  });

  it("selecting a known marker auto-fills its unit and reference range", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [result()] });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add blood test" }));

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "LDL Cholesterol" } });

    expect(screen.getByDisplayValue("mg/dL")).toBeInTheDocument();
    expect(screen.getByDisplayValue("0")).toBeInTheDocument();
    expect(screen.getByDisplayValue("130")).toBeInTheDocument();
  });

  it("submits the auto-filled unit/reference range when saving with a known marker", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [result()] });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add blood test" }));

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "LDL Cholesterol" } });
    fireEvent.change(screen.getAllByRole("spinbutton")[0]!, { target: { value: "95" } });
    fireEvent.click(screen.getByRole("button", { name: "Save blood test" }));

    expect(mockCreateBatchMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        results: [
          expect.objectContaining({
            marker: "LDL Cholesterol",
            value_num: 95,
            unit: "mg/dL",
            reference_low: 0,
            reference_high: 130,
          }),
        ],
      }),
      expect.anything(),
    );
  });

  it("choosing '+ New marker' reveals free text, and 'Choose existing' reverts to the dropdown", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [result()] });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add blood test" }));

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "__new_marker__" } });
    expect(screen.getByPlaceholderText("e.g. LDL Cholesterol")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Choose existing" }));
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("e.g. LDL Cholesterol")).not.toBeInTheDocument();
  });

  it("still shows a marker dropdown with no marker history yet, from the bundled common list", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [] });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add blood test" }));

    expect(screen.queryByPlaceholderText("e.g. LDL Cholesterol")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Total Cholesterol" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "+ New marker…" })).toBeInTheDocument();
  });

  it("selecting a common-list marker with no personal history leaves unit/range blank", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [] });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add blood test" }));

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "TSH" } });

    expect(screen.getByPlaceholderText("mg/dL")).toHaveValue("");
    for (const input of screen.getAllByRole("spinbutton")) {
      expect(input).toHaveValue(null);
    }
  });

  it("clicking Cancel on the add form hides it without submitting", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [] });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add blood test" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "+ Add blood test" })).toBeInTheDocument();
    expect(mockCreateBatchMutate).not.toHaveBeenCalled();
  });

  it("clicking Edit on a result reveals inline inputs, and Save submits the update", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [result()] });
    render(<BloodTestsPanel />);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const valueInput = screen.getByDisplayValue("110");
    fireEvent.change(valueInput, { target: { value: "115" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(mockUpdateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ resultId: 1, value_num: 115 }),
      expect.anything(),
    );
  });

  it("clicking Delete on a result calls the delete mutation with its id", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [result()] });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(mockDeleteResultMutate).toHaveBeenCalledWith(1);
  });

  it("clicking Delete this test calls the panel-delete mutation with the draw date", () => {
    mockUseBloodTests.mockReturnValue({ ...EMPTY, data: [result()] });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Delete this test" }));
    expect(mockDeletePanelMutate).toHaveBeenCalledWith("2026-06-01");
  });

  it("shows the panel's own notes, when set, above the results table", () => {
    mockUseBloodTests.mockReturnValue({
      ...EMPTY,
      data: [result({ notes: "Fasting draw" })],
    });
    render(<BloodTestsPanel />);
    expect(screen.getByText("Fasting draw")).toBeInTheDocument();
  });

  it("clicking Edit lab / notes reveals inputs pre-filled with the panel's own lab/notes", () => {
    mockUseBloodTests.mockReturnValue({
      ...EMPTY,
      data: [result({ lab_name: "Quest Diagnostics", notes: "Fasting draw" })],
    });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Edit lab / notes" }));
    expect(screen.getByDisplayValue("Quest Diagnostics")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Fasting draw")).toBeInTheDocument();
  });

  it("saving the panel-level lab/notes edit updates every marker in the panel", async () => {
    mockUseBloodTests.mockReturnValue({
      ...EMPTY,
      data: [
        result({ id: 1, marker: "LDL", lab_name: "Quest", notes: "Fasting draw" }),
        result({ id: 2, marker: "HDL", lab_name: "Quest", notes: "Fasting draw" }),
      ],
    });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Edit lab / notes" }));
    fireEvent.change(screen.getByDisplayValue("Quest"), { target: { value: "LabCorp" } });
    fireEvent.change(screen.getByDisplayValue("Fasting draw"), {
      target: { value: "Non-fasting" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(mockUpdateMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ resultId: 1, marker: "LDL", lab_name: "LabCorp", notes: "Non-fasting" }),
    );
    expect(mockUpdateMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ resultId: 2, marker: "HDL", lab_name: "LabCorp", notes: "Non-fasting" }),
    );
  });

  it("clicking Cancel on the panel-level edit discards changes without saving", () => {
    mockUseBloodTests.mockReturnValue({
      ...EMPTY,
      data: [result({ lab_name: "Quest", notes: "Fasting draw" })],
    });
    render(<BloodTestsPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Edit lab / notes" }));
    fireEvent.change(screen.getByDisplayValue("Quest"), { target: { value: "LabCorp" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Edit lab / notes" })).toBeInTheDocument();
    expect(mockUpdateMutateAsync).not.toHaveBeenCalled();
  });
});
