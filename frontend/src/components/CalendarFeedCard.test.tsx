import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CalendarFeedCard } from "./CalendarFeedCard";

const mockPublishMutate = vi.fn();
const mockUnpublishMutate = vi.fn();
let statusState: { data?: { enabled: boolean; created_at: string | null }; isLoading: boolean } = {
  data: { enabled: false, created_at: null },
  isLoading: false,
};
let publishState: { isPending: boolean; isError: boolean; data?: { url: string } } = {
  isPending: false,
  isError: false,
};
let unpublishState: { isPending: boolean } = { isPending: false };

vi.mock("../api/queries", () => ({
  useCalendarFeedStatus: () => statusState,
  usePublishCalendarFeed: () => ({ ...publishState, mutate: mockPublishMutate }),
  useUnpublishCalendarFeed: () => ({ ...unpublishState, mutate: mockUnpublishMutate }),
}));

beforeEach(() => {
  statusState = { data: { enabled: false, created_at: null }, isLoading: false };
  publishState = { isPending: false, isError: false };
  unpublishState = { isPending: false };
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

describe("CalendarFeedCard", () => {
  it("shows 'Publish calendar' and no Stop-publishing button when disabled", () => {
    render(<CalendarFeedCard />);
    expect(screen.getByRole("button", { name: "Publish calendar" })).toBeInTheDocument();
    expect(screen.queryByText("Stop publishing")).not.toBeInTheDocument();
  });

  it("publishes and shows the returned URL with a Copy button", () => {
    publishState = {
      isPending: false,
      isError: false,
      data: { url: "https://example.com/share/calendar/abc123.ics" },
    };
    render(<CalendarFeedCard />);

    fireEvent.click(screen.getByRole("button", { name: "Publish calendar" }));

    expect(mockPublishMutate).toHaveBeenCalled();
    expect(
      screen.getByDisplayValue("https://example.com/share/calendar/abc123.ics"),
    ).toBeInTheDocument();
    expect(screen.getByText("Copy")).toBeInTheDocument();
  });

  it("shows 'Rotate link' and 'Stop publishing' when already enabled", () => {
    statusState = { data: { enabled: true, created_at: "2026-09-01T00:00:00Z" }, isLoading: false };
    render(<CalendarFeedCard />);
    expect(screen.getByText("Rotate link")).toBeInTheDocument();
    expect(screen.getByText("Stop publishing")).toBeInTheDocument();
  });

  it("calls the unpublish mutation when Stop publishing is clicked", () => {
    statusState = { data: { enabled: true, created_at: "2026-09-01T00:00:00Z" }, isLoading: false };
    render(<CalendarFeedCard />);

    fireEvent.click(screen.getByText("Stop publishing"));

    expect(mockUnpublishMutate).toHaveBeenCalled();
  });

  it("shows an error message when publishing fails", () => {
    publishState = { isPending: false, isError: true };
    render(<CalendarFeedCard />);
    expect(screen.getByText("Could not publish the calendar feed.")).toBeInTheDocument();
  });
});
