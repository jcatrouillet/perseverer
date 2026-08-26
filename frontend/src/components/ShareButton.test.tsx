import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityShareButton, PeriodShareButton } from "./ShareButton";

const mockActivityShareMutate = vi.fn(
  (_vars: unknown, options?: { onSuccess?: () => void }) => options?.onSuccess?.(),
);
const mockPeriodShareMutate = vi.fn(
  (_vars: unknown, options?: { onSuccess?: () => void }) => options?.onSuccess?.(),
);
let activityShareState: { isPending: boolean; isError: boolean; data?: { url: string } } = {
  isPending: false,
  isError: false,
};
let periodShareState: { isPending: boolean; isError: boolean; data?: { url: string } } = {
  isPending: false,
  isError: false,
};

vi.mock("../api/queries", () => ({
  useCreateActivityShare: () => ({ ...activityShareState, mutate: mockActivityShareMutate }),
  useCreatePeriodShare: () => ({ ...periodShareState, mutate: mockPeriodShareMutate }),
}));

beforeEach(() => {
  activityShareState = { isPending: false, isError: false };
  periodShareState = { isPending: false, isError: false };
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

describe("ActivityShareButton", () => {
  it("creates a share link and shows the URL in a modal", () => {
    activityShareState = {
      isPending: false,
      isError: false,
      data: { url: "https://example.com/share/abc123" },
    };
    render(<ActivityShareButton activityId="act1" />);

    fireEvent.click(screen.getByText("Share"));

    expect(mockActivityShareMutate).toHaveBeenCalled();
    expect(screen.getByDisplayValue("https://example.com/share/abc123")).toBeInTheDocument();
  });

  it("shows an error message when the mutation fails", () => {
    activityShareState = { isPending: false, isError: true };
    render(<ActivityShareButton activityId="act1" />);
    expect(screen.getByText("Could not create a share link.")).toBeInTheDocument();
  });

  it("disables the button while pending", () => {
    activityShareState = { isPending: true, isError: false };
    render(<ActivityShareButton activityId="act1" />);
    expect(screen.getByText("Creating link…")).toBeDisabled();
  });
});

describe("PeriodShareButton", () => {
  it("creates a share link for a period", () => {
    periodShareState = {
      isPending: false,
      isError: false,
      data: { url: "https://example.com/share/xyz789" },
    };
    render(<PeriodShareButton periodType="month" periodStart="2026-06" />);

    fireEvent.click(screen.getByText("Share"));

    expect(mockPeriodShareMutate).toHaveBeenCalled();
    expect(screen.getByDisplayValue("https://example.com/share/xyz789")).toBeInTheDocument();
  });
});
