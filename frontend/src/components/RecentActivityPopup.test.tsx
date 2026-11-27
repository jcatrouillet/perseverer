import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RecentActivityPopup } from "./RecentActivityPopup";

let mobile = true;
let items: unknown[] = [];

vi.mock("../useIsMobile", () => ({ useIsMobile: () => mobile }));
vi.mock("../api/queries", () => ({
  useActivities: () => ({ data: { items } }),
  useActivityRoutes: () => ({ data: undefined }),
}));
vi.mock("./ActivityCard", () => ({
  ActivityCard: ({ activity }: { activity: { id: string } }) => <div>card {activity.id}</div>,
}));

function ended(id: string, hoursAgo: number) {
  const start = new Date(Date.now() - (hoursAgo + 1) * 3_600_000).toISOString();
  return { id, start_time_utc: start, duration_s: 3600, moving_duration_s: null };
}

beforeEach(() => {
  window.sessionStorage.clear();
  mobile = true;
  items = [];
});

describe("RecentActivityPopup", () => {
  it("shows only the last activity finished in the past 8 hours", () => {
    items = [ended("older", 5), ended("latest", 2)];
    render(<RecentActivityPopup />);
    expect(screen.getByText("card latest")).toBeTruthy();
    expect(screen.queryByText("card older")).toBeNull();
  });

  it("shows nothing for an activity that finished more than 8 hours ago", () => {
    items = [ended("old", 10)];
    render(<RecentActivityPopup />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows nothing on a wide screen", () => {
    mobile = false;
    items = [ended("latest", 2)];
    render(<RecentActivityPopup />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("is shown once per session: closing it keeps it from coming back", () => {
    items = [ended("latest", 2)];
    const { unmount } = render(<RecentActivityPopup />);
    fireEvent.click(screen.getByText("Close"));
    expect(screen.queryByRole("dialog")).toBeNull();
    unmount();
    render(<RecentActivityPopup />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
