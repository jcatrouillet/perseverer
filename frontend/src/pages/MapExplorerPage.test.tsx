import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MapExplorerPage } from "./MapExplorerPage";

const point = (id: string, is_virtual: boolean) => ({
  id,
  local_date: "2026-05-01",
  sport: "cycling",
  name: id,
  distance_m: 1000,
  start_lat: 1,
  start_lng: 2,
  is_virtual,
});

vi.mock("../api/queries", () => ({
  useActivityMapPoints: () => ({
    isLoading: false,
    isError: false,
    data: [point("outdoor", false), point("zwift", true)],
  }),
}));
vi.mock("react-leaflet", () => ({
  MapContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CircleMarker: ({ children }: { children: ReactNode }) => (
    <div data-testid="point">{children}</div>
  ),
  Popup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("../components/CartoBasemapLayer", () => ({ CartoBasemapLayer: () => null }));

describe("MapExplorerPage virtual toggle", () => {
  beforeEach(() => localStorage.clear());

  it("shows every activity, and offers the toggle, when some are tagged virtual", () => {
    render(<MapExplorerPage />);
    expect(screen.getByLabelText("Hide virtual activities")).not.toBeChecked();
    expect(screen.getAllByTestId("point")).toHaveLength(2);
  });

  it("hides virtual activities when ticked, and remembers the choice", () => {
    const { unmount } = render(<MapExplorerPage />);
    fireEvent.click(screen.getByLabelText("Hide virtual activities"));
    expect(screen.getAllByTestId("point")).toHaveLength(1);
    expect(screen.getByText("1 of 2 activities shown")).toBeInTheDocument();
    unmount();
    render(<MapExplorerPage />);
    expect(screen.getByLabelText("Hide virtual activities")).toBeChecked();
    expect(screen.getAllByTestId("point")).toHaveLength(1);
  });
});
