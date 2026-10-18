import { fireEvent, render, screen } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityListPage } from "./ActivityListPage";

const mockUseActivities = vi.fn();
const EMPTY = { data: undefined, isLoading: false, isError: false };

vi.mock("../api/queries", () => ({
  useActivities: (...args: unknown[]) => mockUseActivities(...args),
  useActivityRoutes: () => EMPTY,
  useHealthDashboard: () => EMPTY,
  useSleep: () => EMPTY,
}));
vi.mock("../components/ActivitiesTabs", () => ({ ActivitiesTabs: () => null }));
vi.mock("../PersonalizeContext", () => ({
  usePersonalize: () => ({ default_view: "week" }),
}));

function renderAt(path: string) {
  const { hook, history } = memoryLocation({ path, record: true });
  render(
    <Router hook={hook}>
      <ActivityListPage />
    </Router>,
  );
  return history;
}

describe("ActivityListPage filters", () => {
  beforeEach(() => {
    mockUseActivities.mockReset();
    mockUseActivities.mockReturnValue({ ...EMPTY, data: { items: [], total: 120 } });
  });

  it("reads the sport and page from the URL, so Back from an activity restores them", () => {
    renderAt("/activities?sport=cycling&offset=50");
    expect(mockUseActivities).toHaveBeenCalledWith({ sport: "cycling", limit: 50, offset: 50 });
    expect(screen.getByLabelText("Sport")).toHaveValue("cycling");
  });

  it("writes a chosen sport to the URL and resets the page", () => {
    const history = renderAt("/activities?offset=50");
    fireEvent.change(screen.getByLabelText("Sport"), { target: { value: "running" } });
    expect(history[history.length - 1]).toBe("/activities?sport=running");
    expect(mockUseActivities).toHaveBeenLastCalledWith({ sport: "running", limit: 50, offset: 0 });
  });

  it("keeps the sport while paging", () => {
    const history = renderAt("/activities?sport=running");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(history[history.length - 1]).toBe("/activities?sport=running&offset=50");
  });

  it("ignores a malformed offset", () => {
    renderAt("/activities?offset=abc");
    expect(mockUseActivities).toHaveBeenCalledWith({ sport: undefined, limit: 50, offset: 0 });
  });

  it("filters by distance range and tags, in the URL, converted to metres", () => {
    const history = renderAt("/activities?min=5&max=10&race=only&virtual=exclude");
    expect(mockUseActivities).toHaveBeenCalledWith({
      sport: undefined,
      limit: 50,
      offset: 0,
      minDistanceM: 5000,
      maxDistanceM: 10000,
      isRace: true,
      isVirtual: false,
      isCommute: undefined,
    });
    expect(screen.getByLabelText("Race")).toHaveValue("only");
    expect(screen.getByLabelText("Virtual")).toHaveValue("exclude");

    fireEvent.change(screen.getByLabelText("Commute"), { target: { value: "only" } });
    const url = history[history.length - 1]!;
    expect(url).toContain("min=5");
    expect(url).toContain("max=10");
    expect(url).toContain("race=only");
    expect(url).toContain("virtual=exclude");
    expect(url).toContain("commute=only");
  });

  it("goes back to the first page when a filter changes", () => {
    const history = renderAt("/activities?offset=50");
    fireEvent.change(screen.getByLabelText(/Distance from/), { target: { value: "3" } });
    expect(history[history.length - 1]).toBe("/activities?min=3");
  });
});
