import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { BulkImportCard } from "./BulkImportCard";

const mockUploadMutate = vi.fn((_vars: unknown, options?: { onSuccess?: () => void }) =>
  options?.onSuccess?.(),
);
const mockUseLatestJob = vi.fn();
const mockUploadState = vi.fn(() => ({ isPending: false, isError: false }));

vi.mock("../api/queries", () => ({
  useUploadBulkExport: () => ({ mutate: mockUploadMutate, ...mockUploadState() }),
  useLatestJob: (...args: unknown[]) => mockUseLatestJob(...args),
}));

function makeZipFile(name = "export.zip"): File {
  return new File(["dummy content"], name, { type: "application/zip" });
}

describe("BulkImportCard", () => {
  it("defaults the source selector to Garmin export", () => {
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<BulkImportCard />);
    expect(screen.getByLabelText("Source")).toHaveValue("garmin");
  });

  it("submits the selected kind and file", () => {
    mockUseLatestJob.mockReturnValue({ data: undefined });
    const { container } = render(<BulkImportCard />);

    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "strava" } });
    const fileInput = screen.getByLabelText("Export file (.zip)") as HTMLInputElement;
    Object.defineProperty(fileInput, "files", { value: [makeZipFile()] });
    fireEvent.change(fileInput);
    // fireEvent.submit dispatches the submit event directly, bypassing jsdom's native
    // click-triggered constraint validation -- which otherwise silently blocks the submit
    // because a file input's `required` check is tied to its (unsettable-by-tests) `.value`
    // string, not the `.files` list this test populates via Object.defineProperty.
    fireEvent.submit(container.querySelector("form")!);

    expect(mockUploadMutate).toHaveBeenCalledWith(
      { kind: "strava", file: expect.any(File) },
      expect.anything(),
    );
  });

  it("the submit button is disabled until a file is chosen", () => {
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<BulkImportCard />);
    expect(screen.getByText("Upload and import")).toBeDisabled();
  });

  it("says which file failed, how big it is, and the server's reason", () => {
    mockUseLatestJob.mockReturnValue({ data: undefined });
    const detail = "Not enough disk space: the import needs about 1,465 MB.";
    mockUploadState.mockReturnValue({
      isPending: false,
      isError: true,
      error: new ApiError(507, JSON.stringify({ detail }), detail),
      variables: { kind: "garmin", file: new File(["x".repeat(2048)], "export_12494407.zip") },
    } as never);
    render(<BulkImportCard />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      `Could not upload export_12494407.zip (2 KB). ${detail} (HTTP 507)`,
    );
  });
});
