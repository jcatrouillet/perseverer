import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BulkImportCard } from "./BulkImportCard";

const mockUploadMutate = vi.fn((_vars: unknown, options?: { onSuccess?: () => void }) =>
  options?.onSuccess?.(),
);
const mockUseLatestJob = vi.fn();

vi.mock("../api/queries", () => ({
  useUploadBulkExport: () => ({ mutate: mockUploadMutate, isPending: false, isError: false }),
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
});
