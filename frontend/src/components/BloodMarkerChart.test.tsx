import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { BloodTestResultOut } from "../api/types";
import { BloodMarkerChart } from "./BloodMarkerChart";

vi.mock("../api/queries", () => ({
  useUpdateBloodTestResult: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteBloodTestResult: () => ({ mutate: vi.fn(), isPending: false }),
  useBloodTests: () => ({ data: [], isLoading: false, isError: false }),
  useCreateBloodTestBatch: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useDeleteBloodTestPanel: () => ({ mutate: vi.fn(), isPending: false }),
}));

function result(id: number, local_date: string, value_num: number): BloodTestResultOut {
  return {
    id,
    local_date,
    marker: "ALT",
    value_num,
    unit: "U/L",
    reference_low: 15,
    reference_high: 60,
    lab_name: "Some Lab",
    notes: null,
    created_at: "2026-01-01T00:00:00",
    updated_at: "2026-01-01T00:00:00",
  };
}

describe("BloodMarkerChart", () => {
  it("summarises the marker, counting results outside their own reference range", () => {
    render(
      <BloodMarkerChart
        marker="ALT"
        results={[
          result(1, "2015-10-14", 81),
          result(2, "2016-02-26", 57),
          result(3, "2018-03-19", 25),
        ]}
      />,
    );
    expect(screen.getByRole("heading", { name: /ALT/ })).toBeInTheDocument();
    expect(screen.getByText(/3 results, 1 outside the range/)).toBeInTheDocument();
  });

  it("lists the results newest first, flagging only the out-of-range one", () => {
    render(
      <BloodMarkerChart
        marker="ALT"
        results={[result(1, "2015-10-14", 81), result(3, "2018-03-19", 25)]}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("2018");
    expect(rows[1]).toHaveTextContent("2015");
    expect(screen.getAllByText("Out of range")).toHaveLength(1);
  });

  it("says when the newest result is outside its range", () => {
    render(<BloodMarkerChart marker="ALT" results={[result(1, "2015-10-14", 81)]} />);
    expect(screen.getByText(/outside its reference range/)).toBeInTheDocument();
    expect(screen.getByText(/1 result,/)).toBeInTheDocument();
  });

  it("shows a description of what the marker represents", () => {
    render(<BloodMarkerChart marker="ALT" results={[result(1, "2015-10-14", 81)]} />);
    expect(screen.getByText(/Alanine aminotransferase/)).toBeInTheDocument();
  });
});
