import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PosterOut } from "../api/types";
import { PostersSection, sourceLabel, sportLabel } from "./PostersSection";

const mocks = { create: vi.fn(), remove: vi.fn() };
const mockPosters = vi.fn();

vi.mock("../api/queries", () => ({
  usePosters: () => mockPosters(),
  usePosterSports: () => ({
    isLoading: false,
    data: [
      { sport: "running", activities: 10, with_route: 8 },
      { sport: "cycling", activities: 4, with_route: 0 },
    ],
  }),
  useHeatmapDefaults: () => ({
    data: {
      available: true,
      center_lat: 48.85,
      center_lon: 2.35,
      radius_km: 2.5,
      source: "home_address",
      activities: 8,
    },
  }),
  useCreatePoster: () => ({ mutate: mocks.create, isPending: false, isError: false }),
  useDeletePoster: () => ({ mutate: mocks.remove, isPending: false }),
}));

vi.mock("../formatDistance", () => ({
  useDistanceFormat: () => ({ unitLabel: "km", metersToDisplay: (m: number) => m / 1000 }),
}));

// Leaflet needs a real layout engine; the picker is covered by its own props here.
vi.mock("./PosterMapPicker", () => ({
  default: ({ radiusKm }: { radiusKm: number }) => <div data-testid="map">{radiusKm} km</div>,
}));

function poster(over: Partial<PosterOut> = {}): PosterOut {
  return {
    id: "p1",
    kind: "grid",
    year: 2025,
    sports: [],
    include_virtual: false,
    center_lat: null,
    center_lon: null,
    radius_km: null,
    status: "done",
    error: null,
    filename: "All-2025-all-grid-4000.png",
    size_bytes: 700_000,
    activity_count: 8,
    created_at: "2025-12-31T10:00:00",
    finished_at: "2025-12-31T10:00:09",
    ...over,
  };
}

describe("PostersSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPosters.mockReturnValue({ data: [] });
  });

  it("offers only the sports that have a GPS trace for the grid", () => {
    render(<PostersSection year={2025} />);
    expect(screen.getByLabelText("Running (8)")).toBeInTheDocument();
    expect(screen.queryByText(/Cycling/)).not.toBeInTheDocument();
  });

  it("generates the grid for one sport, replacing the previous pick", () => {
    render(<PostersSection year={2025} />);
    fireEvent.click(screen.getByLabelText("Running (8)"));
    fireEvent.click(screen.getByRole("button", { name: "Generate poster" }));
    expect(mocks.create).toHaveBeenCalledWith({
      kind: "grid",
      year: 2025,
      sports: ["running"],
      include_virtual: false,
    });
  });

  it("lets the calendar take several sports", () => {
    render(<PostersSection year={2025} />);
    fireEvent.click(screen.getByRole("radio", { name: /Calendar/ }));
    fireEvent.click(screen.getByLabelText("Running (10)"));
    fireEvent.click(screen.getByLabelText("Cycling (4)"));
    fireEvent.click(screen.getByRole("button", { name: "Generate poster" }));
    expect(mocks.create).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "calendar", sports: ["running", "cycling"] }),
    );
  });

  it("starts the heatmap from the home address and its default range", () => {
    render(<PostersSection year={2025} />);
    fireEvent.click(screen.getByRole("radio", { name: /Heatmap/ }));
    expect(screen.getByText(/Centred on your home address, reaching 2.5 km/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Generate poster" }));
    expect(mocks.create).toHaveBeenCalledWith({
      kind: "heatmap",
      year: 2025,
      sports: [],
      include_virtual: false,
      center_lat: 48.85,
      center_lon: 2.35,
      radius_km: 2.5,
    });
  });

  it("sends a range typed by the athlete", async () => {
    render(<PostersSection year={2025} />);
    fireEvent.click(screen.getByRole("radio", { name: /Heatmap/ }));
    fireEvent.change(screen.getByLabelText(/Range/), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate poster" }));
    expect(mocks.create).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "heatmap", radius_km: 10 }),
    );
  });

  it("shows the progress of a poster being drawn and a finished one's actions", () => {
    mockPosters.mockReturnValue({
      data: [
        poster({ id: "p2", status: "running", kind: "circular" }),
        poster(),
        poster({ id: "p3", kind: "calendar", status: "failed", error: "No activity matched." }),
      ],
    });
    render(<PostersSection year={2025} />);
    expect(screen.getByText("Drawing…")).toBeInTheDocument();
    expect(screen.getByText("No activity matched.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Download" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Delete Grid · All sports" }));
    expect(mocks.remove).toHaveBeenCalledWith("p1");
  });

  it("words the sports and the centre's origin", () => {
    expect(sportLabel("trail_running")).toBe("Trail running");
    expect(sourceLabel("profile_home")).toBe("your home");
    expect(sourceLabel(null)).toBe("the map");
  });
});
