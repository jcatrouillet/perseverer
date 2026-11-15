import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { GarminAuthStatusOut } from "../api/types";
import { GarminConnectCard } from "./GarminConnectCard";

const mockUseGarminStatus = vi.fn();
const mockLoginMutate = vi.fn();
const mockSyncMutate = vi.fn();
const mockSaveKeyMutate = vi.fn();
const mockRemoveKeyMutate = vi.fn();
const mockUseLatestJob = vi.fn();

vi.mock("../api/queries", () => ({
  useGarminStatus: () => mockUseGarminStatus(),
  useGarminLogin: () => ({
    mutate: mockLoginMutate,
    isPending: false,
    isError: false,
    isSuccess: false,
  }),
  useSaveGarminApiKey: () => ({
    mutate: mockSaveKeyMutate,
    isPending: false,
    isError: false,
    isSuccess: false,
  }),
  useRemoveGarminApiKey: () => ({ mutate: mockRemoveKeyMutate, isPending: false }),
  useTriggerGarminSync: () => ({ mutate: mockSyncMutate, isPending: false }),
  useLatestJob: (...args: unknown[]) => mockUseLatestJob(...args),
}));

const NOT_CONNECTED: GarminAuthStatusOut = {
  token_store_present: false,
  token_store_age_days: null,
  auth_mode: null,
  api_key_age_days: null,
  last_sync_status: null,
  last_sync_at: null,
  last_sync_error: null,
  staleness_severity: null,
  staleness_message: null,
};

const CONNECTED: GarminAuthStatusOut = {
  token_store_present: true,
  token_store_age_days: 3,
  auth_mode: "login",
  api_key_age_days: null,
  last_sync_status: "success",
  last_sync_at: "2026-08-20T00:00:00Z",
  last_sync_error: null,
  staleness_severity: null,
  staleness_message: null,
};

const STALE: GarminAuthStatusOut = {
  ...CONNECTED,
  last_sync_status: "failed",
  staleness_severity: "warning",
  staleness_message: "garmin_connect has been failing for 8 day(s) as of 2026-08-25.",
};

describe("GarminConnectCard", () => {
  it("shows not-connected status", () => {
    mockUseGarminStatus.mockReturnValue({ data: NOT_CONNECTED });
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<GarminConnectCard />);
    expect(screen.getByText("Not connected yet.")).toBeInTheDocument();
    expect(screen.getByText("No sync has run yet.")).toBeInTheDocument();
  });

  it("shows connected status with token age and last sync result", () => {
    mockUseGarminStatus.mockReturnValue({ data: CONNECTED });
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<GarminConnectCard />);
    expect(screen.getByText(/Connected — session established 3 days ago/)).toBeInTheDocument();
    expect(screen.getByText("Last sync succeeded.")).toBeInTheDocument();
  });

  it("submits the login form with username and password", () => {
    mockUseGarminStatus.mockReturnValue({ data: NOT_CONNECTED });
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<GarminConnectCard />);

    fireEvent.change(screen.getByLabelText("Garmin username"), {
      target: { value: "me@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Garmin password"), {
      target: { value: "hunter2" },
    });
    fireEvent.click(screen.getByText("Log in"));

    expect(mockLoginMutate).toHaveBeenCalledWith(
      { username: "me@example.com", password: "hunter2" },
      expect.anything(),
    );
  });

  it("clicking Sync now triggers the sync mutation", () => {
    mockUseGarminStatus.mockReturnValue({ data: CONNECTED });
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<GarminConnectCard />);

    fireEvent.click(screen.getByText("Sync now"));

    expect(mockSyncMutate).toHaveBeenCalled();
  });

  it("shows no staleness warning once the last sync succeeded", () => {
    mockUseGarminStatus.mockReturnValue({ data: CONNECTED });
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<GarminConnectCard />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the staleness warning once syncs start failing", () => {
    mockUseGarminStatus.mockReturnValue({ data: STALE });
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<GarminConnectCard />);
    expect(screen.getByRole("alert")).toHaveTextContent(STALE.staleness_message!);
  });

  it("saves a personal API key", () => {
    mockUseGarminStatus.mockReturnValue({ data: NOT_CONNECTED });
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<GarminConnectCard />);

    fireEvent.change(screen.getByLabelText("Personal API key"), {
      target: { value: "secret-key" },
    });
    fireEvent.click(screen.getByText("Save key"));
    expect(mockSaveKeyMutate).toHaveBeenCalledWith(
      { api_key: "secret-key" },
      expect.objectContaining({ onSettled: expect.any(Function) }),
    );
    expect(screen.queryByText("Remove key")).not.toBeInTheDocument();
  });

  it("shows a key connection and lets the athlete remove it", () => {
    mockUseGarminStatus.mockReturnValue({
      data: { ...CONNECTED, auth_mode: "personal_api_key", api_key_age_days: 12 },
    });
    mockUseLatestJob.mockReturnValue({ data: undefined });
    render(<GarminConnectCard />);
    expect(
      screen.getByText(/Connected with a personal API key — saved 12 days ago/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("Remove key"));
    expect(mockRemoveKeyMutate).toHaveBeenCalled();
  });
});
