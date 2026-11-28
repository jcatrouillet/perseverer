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
  window.localStorage.clear();
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

  it("is shown once per activity: closing it keeps it from coming back, a newer one shows", () => {
    items = [ended("latest", 2)];
    const first = render(<RecentActivityPopup />);
    fireEvent.click(screen.getByText("Close"));
    expect(screen.queryByRole("dialog")).toBeNull();
    first.unmount();
    const second = render(<RecentActivityPopup />);
    expect(screen.queryByRole("dialog")).toBeNull();
    second.unmount();
    items = [ended("latest", 2), ended("newer", 1)];
    render(<RecentActivityPopup />);
    expect(screen.getByText("card newer")).toBeTruthy();
  });
});
