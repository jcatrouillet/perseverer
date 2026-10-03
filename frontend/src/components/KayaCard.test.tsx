import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { KayaStatusOut } from "../api/types";
import { KayaCard } from "./KayaCard";

const mockUseKayaStatus = vi.fn();
const mockLoginMutate = vi.fn();
const mockSyncMutate = vi.fn();
const mockUseLatestJob = vi.fn();
let loginState: { isPending: boolean; isError: boolean; isSuccess: boolean; error: unknown };

vi.mock("../api/queries", () => ({
  useKayaStatus: () => mockUseKayaStatus(),
  useKayaLogin: () => ({ mutate: mockLoginMutate, ...loginState }),
  useTriggerKayaSync: () => ({ mutate: mockSyncMutate, isPending: false, isError: false }),
  useLatestJob: (...args: unknown[]) => mockUseLatestJob(...args),
}));

const NOT_CONNECTED: KayaStatusOut = {
  session_present: false,
  session_age_days: null,
  last_sync_status: null,
  last_sync_at: null,
  last_sync_error: null,
};
const CONNECTED: KayaStatusOut = {
  session_present: true,
  session_age_days: 2,
  last_sync_status: "success",
  last_sync_at: "2026-10-03T04:15:00Z",
  last_sync_error: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  loginState = { isPending: false, isError: false, isSuccess: false, error: null };
  mockUseLatestJob.mockReturnValue({ data: undefined });
});

describe("KayaCard", () => {
  it("shows not-connected and keeps Sync now disabled until a session exists", () => {
    mockUseKayaStatus.mockReturnValue({ data: NOT_CONNECTED });
    render(<KayaCard />);
    expect(screen.getByText("Not connected yet.")).toBeInTheDocument();
    expect(screen.getByText("No sync has run yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sync now" })).toBeDisabled();
  });

  it("shows the session age and the last sync result, and syncs on click", () => {
    mockUseKayaStatus.mockReturnValue({ data: CONNECTED });
    render(<KayaCard />);
    expect(screen.getByText(/Connected — signed in 2 days ago/)).toBeInTheDocument();
    expect(screen.getByText("Last sync succeeded.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
    expect(mockSyncMutate).toHaveBeenCalled();
  });

  it("surfaces a failed last sync with its error", () => {
    mockUseKayaStatus.mockReturnValue({
      data: { ...CONNECTED, last_sync_status: "failed", last_sync_error: "Kaya session expired" },
    });
    render(<KayaCard />);
    expect(screen.getByText("Last sync failed: Kaya session expired")).toBeInTheDocument();
  });

  it("refreshes the status line once the sync job settles", () => {
    const refetch = vi.fn();
    mockUseKayaStatus.mockReturnValue({ data: CONNECTED, refetch });
    mockUseLatestJob.mockReturnValue({ data: { status: "success" } });
    render(<KayaCard />);
    expect(refetch).toHaveBeenCalled();
  });

  it("does not refetch while the job is still running", () => {
    const refetch = vi.fn();
    mockUseKayaStatus.mockReturnValue({ data: CONNECTED, refetch });
    mockUseLatestJob.mockReturnValue({ data: { status: "running" } });
    render(<KayaCard />);
    expect(refetch).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Syncing…" })).toBeDisabled();
  });

  it("submits the email and password to log in", () => {
    mockUseKayaStatus.mockReturnValue({ data: NOT_CONNECTED });
    render(<KayaCard />);
    fireEvent.change(screen.getByLabelText("Kaya email"), { target: { value: "me@example.com" } });
    fireEvent.change(screen.getByLabelText("Kaya password"), { target: { value: "hunter2" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(mockLoginMutate).toHaveBeenCalledWith(
      { email: "me@example.com", password: "hunter2" },
      expect.any(Object),
    );
  });

  it("says plainly when the credentials were rejected", () => {
    mockUseKayaStatus.mockReturnValue({ data: NOT_CONNECTED });
    loginState = {
      isPending: false,
      isError: true,
      isSuccess: false,
      error: new Error("request failed (400)"),
    };
    render(<KayaCard />);
    expect(screen.getByRole("alert")).toHaveTextContent("Incorrect Kaya email or password.");
  });
});
