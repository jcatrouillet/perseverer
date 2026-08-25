import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { JobStatusOut } from "../api/types";
import { RebuildCard } from "./RebuildCard";

const mockRebuildMutate = vi.fn(
  (_vars: unknown, options?: { onSuccess?: () => void }) => options?.onSuccess?.(),
);
const mockUseLatestJob = vi.fn();

vi.mock("../api/queries", () => ({
  useTriggerRebuild: () => ({ mutate: mockRebuildMutate, isPending: false }),
  useLatestJob: (...args: unknown[]) => mockUseLatestJob(...args),
}));

describe("RebuildCard", () => {
  it("triggers the rebuild mutation on click", () => {
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<RebuildCard />);

    fireEvent.click(screen.getByText("Rebuild now"));

    expect(mockRebuildMutate).toHaveBeenCalled();
  });

  it("does not show a job status before anything is triggered", () => {
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<RebuildCard />);
    expect(screen.queryByText(/Running|Done|Failed/)).not.toBeInTheDocument();
  });

  it("shows a failure banner once triggered and the job reports failed", () => {
    mockUseLatestJob.mockReturnValue({
      data: {
        source: "rebuild",
        status: "failed",
        started_at: "2026-08-20T00:00:00Z",
        finished_at: "2026-08-20T00:00:05Z",
        items_seen: 0,
        items_new: 0,
        error_count: 1,
        first_error: "archive not found",
      } satisfies JobStatusOut,
    });
    render(<RebuildCard />);

    fireEvent.click(screen.getByText("Rebuild now"));

    expect(screen.getByText("Failed: archive not found")).toBeInTheDocument();
  });
});
