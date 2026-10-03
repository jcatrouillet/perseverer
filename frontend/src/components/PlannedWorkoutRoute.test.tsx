import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PlannedRouteOut } from "../api/types";
import { PlannedWorkoutRoute } from "./PlannedWorkoutRoute";

vi.mock("./ActivityMap", () => ({
  ActivityMap: ({ encodedPolyline }: { encodedPolyline: string }) => (
    <div data-testid="route-map">{encodedPolyline}</div>
  ),
}));

const ROUTE: PlannedRouteOut = {
  name: "Dam loop",
  distance_m: 12345,
  elevation_gain_m: 341.6,
  polyline: "_p~iF~ps|U_ulLnnqC",
  uploaded_at: "2026-10-02T10:00:00",
};

describe("PlannedWorkoutRoute", () => {
  it("offers to attach a GPX when there is no route yet, and passes the chosen file up", () => {
    const onAttach = vi.fn();
    render(
      <PlannedWorkoutRoute
        route={null}
        onAttach={onAttach}
        onRemove={vi.fn()}
        isBusy={false}
        error={null}
      />,
    );
    expect(screen.getByRole("button", { name: "Attach GPX route" })).toBeInTheDocument();
    expect(screen.queryByTestId("route-map")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove route" })).not.toBeInTheDocument();

    const file = new File(["<gpx/>"], "dam.gpx", { type: "application/gpx+xml" });
    fireEvent.change(screen.getByLabelText("GPX file"), { target: { files: [file] } });
    expect(onAttach).toHaveBeenCalledWith(file);
  });

  it("shows the route's name, distance, climb and map, with Replace and Remove", () => {
    const onRemove = vi.fn();
    render(
      <PlannedWorkoutRoute
        route={ROUTE}
        onAttach={vi.fn()}
        onRemove={onRemove}
        isBusy={false}
        error={null}
      />,
    );
    expect(screen.getByText("Dam loop")).toBeInTheDocument();
    expect(screen.getByText(/12\.3 km · \+342 m/)).toBeInTheDocument();
    expect(screen.queryByText(/km km/)).not.toBeInTheDocument();
    expect(screen.getByText(/\+342 m/)).toBeInTheDocument();
    expect(screen.getByTestId("route-map")).toHaveTextContent(ROUTE.polyline);
    expect(screen.getByRole("button", { name: "Replace GPX" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove route" }));
    expect(onRemove).toHaveBeenCalled();
  });

  it("disables the buttons while busy and surfaces an upload error", () => {
    render(
      <PlannedWorkoutRoute
        route={ROUTE}
        onAttach={vi.fn()}
        onRemove={vi.fn()}
        isBusy
        error="the GPX file has fewer than two track points"
      />,
    );
    expect(screen.getByRole("button", { name: "Uploading…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove route" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("fewer than two track points");
  });
});
