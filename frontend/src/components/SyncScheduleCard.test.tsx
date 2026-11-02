import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SyncScheduleOut } from "../api/types";
import { formatNextRun, SyncScheduleCard } from "./SyncScheduleCard";

const mockSave = vi.fn();
let schedule: SyncScheduleOut;

vi.mock("../api/queries", () => ({
  useSyncSchedule: () => ({ data: schedule }),
  useSetSyncSchedule: () => ({
    mutate: mockSave,
    isPending: false,
    isError: false,
    isSuccess: false,
  }),
}));

beforeEach(() => {
  mockSave.mockClear();
  schedule = {
    window_minutes: 5,
    sources: [
      {
        source: "garmin_connect",
        label: "Garmin Connect",
        configured: true,
        enabled: true,
        time: "21:15",
        is_default: true,
        timezone: "America/Los_Angeles",
        next_run_at: "2026-10-31T04:15:00Z",
      },
      {
        source: "eufy",
        label: "Eufy scale",
        configured: false,
        enabled: true,
        time: "21:15",
        is_default: true,
        timezone: "America/Los_Angeles",
        next_run_at: null,
      },
      {
        source: "kaya",
        label: "Kaya",
        configured: true,
        enabled: true,
        time: "07:00",
        is_default: false,
        timezone: "America/Los_Angeles",
        next_run_at: "2026-10-31T14:00:00Z",
      },
    ],
  };
});

describe("SyncScheduleCard", () => {
  it("shows each source's time and when it next runs, on the athlete's clock", () => {
    render(<SyncScheduleCard />);
    expect(screen.getByLabelText("Garmin Connect sync time")).toHaveValue("21:15");
    expect(screen.getByLabelText("Kaya sync time")).toHaveValue("07:00");
    expect(screen.getByText(/Next: Fri 21:15 \(default time\)/)).toBeInTheDocument();
    expect(screen.getByText(/Next: Sat 07:00/)).toBeInTheDocument();
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.getByLabelText("Eufy scale sync time")).toBeDisabled();
  });

  it("keeps an untouched default as the default and saves a changed time", () => {
    render(<SyncScheduleCard />);
    fireEvent.change(screen.getByLabelText("Kaya sync time"), { target: { value: "08:30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockSave).toHaveBeenCalledWith({
      sources: [
        { source: "garmin_connect", time: null, enabled: true },
        { source: "eufy", time: null, enabled: true },
        { source: "kaya", time: "08:30", enabled: true },
      ],
    });
  });

  it("can switch a source off", () => {
    render(<SyncScheduleCard />);
    fireEvent.click(screen.getByLabelText("Kaya"));
    expect(screen.getByLabelText("Kaya sync time")).toBeDisabled();
    expect(screen.getByText("Off")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockSave).toHaveBeenCalledWith(
      expect.objectContaining({
        sources: expect.arrayContaining([{ source: "kaya", time: "07:00", enabled: false }]),
      }),
    );
  });
});

describe("formatNextRun", () => {
  it("reads the instant on the given zone's clock, 24h or 12h", () => {
    expect(formatNextRun("2026-10-31T04:15:00Z", "America/Los_Angeles", false)).toBe("Fri 21:15");
    expect(formatNextRun("2026-10-31T04:15:00Z", "America/Los_Angeles", true)).toBe("Fri 9:15 PM");
  });
});
