import { fireEvent, render, screen } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CompareRunsPage } from "./CompareRunsPage";

const mockUseActivitySearch = vi.fn();
const EMPTY = { data: undefined, isLoading: false, isError: false };

vi.mock("../api/queries", () => ({
  useActivitySearch: (...args: unknown[]) => mockUseActivitySearch(...args),
  useActivitiesByIds: () => EMPTY,
  useActivityStreams: () => [],
}));
vi.mock("../components/ActivitiesTabs", () => ({ ActivitiesTabs: () => null }));
vi.mock("../components/CompareCharts", () => ({ CompareCharts: () => null }));

function renderAt(path: string) {
  const { hook, history } = memoryLocation({ path, record: true });
  render(
    <Router hook={hook}>
      <CompareRunsPage />
    </Router>,
  );
  return history;
}

describe("CompareRunsPage search filters", () => {
  beforeEach(() => {
    mockUseActivitySearch.mockReset();
    mockUseActivitySearch.mockReturnValue({ ...EMPTY, data: { items: [], total: 0 } });
  });

  it("searches every run until a tag filter is chosen", () => {
    renderAt("/activities/compare");
    expect(mockUseActivitySearch).toHaveBeenLastCalledWith(
      expect.objectContaining({ isRace: undefined, isVirtual: undefined, isCommute: undefined }),
      "running",
    );
  });

  it("passes the race, virtual and commute choices to the search", () => {
    renderAt("/activities/compare");
    fireEvent.change(screen.getByLabelText("Race"), { target: { value: "only" } });
    fireEvent.change(screen.getByLabelText("Virtual"), { target: { value: "exclude" } });
    fireEvent.change(screen.getByLabelText("Commute"), { target: { value: "exclude" } });
    expect(mockUseActivitySearch).toHaveBeenLastCalledWith(
      expect.objectContaining({ isRace: true, isVirtual: false, isCommute: false }),
      "running",
    );
  });
});

describe("CompareRunsPage sport", () => {
  beforeEach(() => {
    mockUseActivitySearch.mockReset();
    mockUseActivitySearch.mockReturnValue({ ...EMPTY, data: { items: [], total: 0 } });
  });

  it("compares runs by default", () => {
    renderAt("/activities/compare");
    expect(screen.getByRole("heading", { name: "Compare runs" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Add runs" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Running" })).toHaveAttribute("aria-pressed", "true");
  });

  it("compares rides when the address says so, searching cycling activities", () => {
    renderAt("/activities/compare?sport=cycling");
    expect(screen.getByRole("heading", { name: "Compare rides" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Add rides" })).toBeInTheDocument();
    expect(screen.getByText(/speed, power, heart rate, cadence and elevation/)).toBeInTheDocument();
    expect(mockUseActivitySearch).toHaveBeenLastCalledWith(expect.anything(), "cycling");
  });

  it("switches sport from the page and starts a fresh comparison", () => {
    const history = renderAt("/activities/compare?ids=a,b");
    fireEvent.click(screen.getByRole("button", { name: "Cycling" }));
    expect(history[history.length - 1]).toBe("/activities/compare?sport=cycling");
    fireEvent.click(screen.getByRole("button", { name: "Running" }));
    expect(history[history.length - 1]).toBe("/activities/compare");
  });
});
