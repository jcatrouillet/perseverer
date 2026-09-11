import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PlannedRaceOut } from "../api/types";
import { PlannedRaceForm } from "./PlannedRaceForm";

const mockUsePlannedRacesForDate = vi.fn();
const mockCreate = vi.fn();
const mockUpdate = vi.fn();
const mockDelete = vi.fn();

vi.mock("../api/queries", () => ({
  usePlannedRacesForDate: (...args: unknown[]) => mockUsePlannedRacesForDate(...args),
  useCreatePlannedRace: () => ({ mutate: mockCreate, isPending: false, isError: false }),
  useUpdatePlannedRace: () => ({ mutate: mockUpdate, isPending: false, isError: false }),
  useDeletePlannedRace: () => ({ mutate: mockDelete, isPending: false }),
}));

const NONE = { data: [], isLoading: false, isError: false };

const RACE: PlannedRaceOut = {
  id: 1,
  local_date: "2026-04-12",
  name: "Paris Marathon",
  sport: "running",
  distance_m: 42195,
  scheduled_time: "09:00",
  target_duration_s: 14340, // 3:59:00
  days_until: 30,
  predicted_duration_s: 13930, // 3:52:10 -- faster than target, on track
};

describe("PlannedRaceForm", () => {
  it("shows an 'Add a race' button when nothing is scheduled", () => {
    mockUsePlannedRacesForDate.mockReturnValue(NONE);
    render(<PlannedRaceForm localDate="2026-09-01" />);
    expect(screen.getByText("Add a race")).toBeInTheDocument();
  });

  it("shows the race name, distance, target, and on-track prediction line", () => {
    mockUsePlannedRacesForDate.mockReturnValue({ data: [RACE], isLoading: false, isError: false });
    render(<PlannedRaceForm localDate="2026-04-12" />);

    expect(screen.getByText("Paris Marathon")).toBeInTheDocument();
    expect(screen.getByText(/42.2 km/)).toBeInTheDocument();
    expect(screen.getByText(/Target sub 3:59:00/)).toBeInTheDocument();
    expect(screen.getByText(/in 30 days/)).toBeInTheDocument();
    expect(screen.getByText(/Predicted 3:52:10 — on track/)).toBeInTheDocument();
  });

  it("shows an over-target prediction line when the target is faster than predicted", () => {
    mockUsePlannedRacesForDate.mockReturnValue({
      data: [{ ...RACE, target_duration_s: 13000 }], // 3:36:40, faster than the 3:52:10 prediction
      isLoading: false,
      isError: false,
    });
    render(<PlannedRaceForm localDate="2026-04-12" />);
    expect(screen.getByText(/over target/)).toBeInTheDocument();
  });

  it("creating a race sends the preset distance and target seconds", () => {
    mockUsePlannedRacesForDate.mockReturnValue(NONE);
    render(<PlannedRaceForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Add a race"));

    fireEvent.change(screen.getByPlaceholderText("e.g. Paris Marathon"), {
      target: { value: "Fall 10K" },
    });
    fireEvent.change(screen.getByDisplayValue("10K"), { target: { value: "10000" } });
    fireEvent.change(screen.getByPlaceholderText("mm"), { target: { value: "40" } });
    fireEvent.click(screen.getByText("Save"));

    expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        local_date: "2026-09-01",
        name: "Fall 10K",
        distance_m: 10000,
        target_duration_s: 2400,
      }),
      expect.anything(),
    );
  });

  it("switching distance to Custom reveals a km field", () => {
    mockUsePlannedRacesForDate.mockReturnValue(NONE);
    render(<PlannedRaceForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Add a race"));

    expect(screen.queryByPlaceholderText("e.g. 15")).not.toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue("10K"), { target: { value: "custom" } });
    expect(screen.getByPlaceholderText("e.g. 15")).toBeInTheDocument();
  });

  it("Edit pre-fills the form from the existing race, and Save calls update", () => {
    mockUsePlannedRacesForDate.mockReturnValue({ data: [RACE], isLoading: false, isError: false });
    render(<PlannedRaceForm localDate="2026-04-12" />);
    fireEvent.click(screen.getByText("Edit"));

    expect(screen.getByDisplayValue("Paris Marathon")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Save"));

    expect(mockUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ raceId: 1, name: "Paris Marathon", distance_m: 42195 }),
      expect.anything(),
    );
  });

  it("Delete calls the delete mutation with the race's id", () => {
    mockUsePlannedRacesForDate.mockReturnValue({ data: [RACE], isLoading: false, isError: false });
    render(<PlannedRaceForm localDate="2026-04-12" />);
    fireEvent.click(screen.getByText("Delete"));
    expect(mockDelete).toHaveBeenCalledWith(1);
  });
});
