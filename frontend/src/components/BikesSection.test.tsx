import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BikeOut, TireOut } from "../api/types";
import { BikesSection } from "./BikesSection";

const mocks = {
  createBike: vi.fn(),
  retireBike: vi.fn(),
  setDefault: vi.fn(),
  clearDefault: vi.fn(),
  createTire: vi.fn(),
  installTire: vi.fn(),
  removeTire: vi.fn(),
  retireTire: vi.fn(),
};
const mockUseBikes = vi.fn();
const mockUseTires = vi.fn();

vi.mock("../api/queries", () => ({
  useBikes: (...args: unknown[]) => mockUseBikes(...args),
  useTires: (...args: unknown[]) => mockUseTires(...args),
  useCreateBike: () => ({ mutate: mocks.createBike, isPending: false, isError: false }),
  useRetireBike: () => ({ mutate: mocks.retireBike, isPending: false }),
  useSetDefaultBike: () => ({ mutate: mocks.setDefault, isPending: false }),
  useClearDefaultBike: () => ({ mutate: mocks.clearDefault, isPending: false }),
  useCreateTire: () => ({ mutate: mocks.createTire, isPending: false, isError: false }),
  useInstallTire: () => ({ mutate: mocks.installTire, isPending: false }),
  useRemoveTire: () => ({ mutate: mocks.removeTire, isPending: false }),
  useRetireTire: () => ({ mutate: mocks.retireTire, isPending: false }),
}));

function tire(overrides: Partial<TireOut> = {}): TireOut {
  return {
    id: "t1",
    brand: "Continental",
    model: "GP5000",
    size: "700x28",
    comments: null,
    initial_distance_km: 0,
    max_distance_km: 5000,
    distance_km: 1250,
    remaining_km: 3750,
    over_limit: false,
    retired: false,
    bike_id: "b1",
    bike_name: "Canyon Endurace",
    position: "front",
    installed_at: "2026-01-01T00:00:00",
    created_at: "2026-01-01T00:00:00",
    ...overrides,
  };
}

function bike(overrides: Partial<BikeOut> = {}): BikeOut {
  return {
    id: "b1",
    brand: "Canyon",
    model: "Endurace",
    year: 2023,
    size: "M",
    comments: null,
    initial_distance_km: 0,
    distance_km: 3400,
    retired: false,
    default_categories: ["cycling"],
    front_tire: tire(),
    rear_tire: null,
    created_at: "2026-01-01T00:00:00",
    ...overrides,
  };
}

function given(bikes: BikeOut[], tires: TireOut[] = []) {
  mockUseBikes.mockReturnValue({ data: bikes, isLoading: false });
  mockUseTires.mockReturnValue({ data: tires, isLoading: false });
}

describe("BikesSection", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((m) => m.mockClear());
  });

  it("shows a bike with its year, size, distance, defaults and both tire slots", () => {
    given([bike()]);
    render(<BikesSection />);
    expect(screen.getByRole("heading", { name: "Canyon Endurace" })).toBeInTheDocument();
    expect(screen.getByText("2023")).toBeInTheDocument();
    expect(screen.getByText("Size M")).toBeInTheDocument();
    expect(screen.getByText("3400 km")).toBeInTheDocument();
    expect(screen.getAllByText("All cycling").length).toBeGreaterThan(0);
    // Front tire installed with its progress; the rear slot is empty.
    expect(screen.getByText("Continental GP5000")).toBeInTheDocument();
    expect(screen.getByText("1250 km")).toBeInTheDocument();
    expect(screen.getByText("3750 km remaining")).toBeInTheDocument();
    expect(screen.getByText("No rear tire")).toBeInTheDocument();
  });

  it("shows a tire's comment on its card", () => {
    given([bike({ front_tire: tire({ comments: "Run at 70 psi" }) })]);
    render(<BikesSection />);
    expect(screen.getByText("Run at 70 psi")).toBeInTheDocument();
  });

  it("a bike without tires is fine, and says so per slot", () => {
    given([bike({ front_tire: null, rear_tire: null })]);
    render(<BikesSection />);
    expect(screen.getByText("No front tire")).toBeInTheDocument();
    expect(screen.getByText("No rear tire")).toBeInTheDocument();
  });

  it("flags a tire that reached its limit", () => {
    given([bike({ front_tire: tire({ over_limit: true, distance_km: 5100 }) })]);
    render(<BikesSection />);
    expect(screen.getByText("Limit of 5000 km reached")).toBeInTheDocument();
  });

  it("sets and clears the default for a category from the bike card", () => {
    given([bike()]);
    render(<BikesSection />);
    fireEvent.click(screen.getByLabelText("Canyon Endurace is the default for Road cycling"));
    expect(mocks.setDefault).toHaveBeenCalledWith({ category: "road", bikeId: "b1" });
    fireEvent.click(screen.getByLabelText("Canyon Endurace is the default for All cycling"));
    expect(mocks.clearDefault).toHaveBeenCalledWith("cycling");
  });

  it("offers every category", () => {
    given([bike()]);
    render(<BikesSection />);
    for (const label of [
      "All cycling",
      "Road cycling",
      "Mountain biking",
      "Gravel bike",
      "E-bike",
      "eMTB",
      "Bike commute",
    ]) {
      expect(
        screen.getByLabelText(`Canyon Endurace is the default for ${label}`),
      ).toBeInTheDocument();
    }
  });

  it("fits a new tire into an empty slot with the entered brand, model, size and limits", () => {
    given([bike({ front_tire: null })]);
    render(<BikesSection />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add front tire" }));
    fireEvent.change(screen.getByLabelText("Tire brand"), { target: { value: "Schwalbe" } });
    fireEvent.change(screen.getByLabelText("Tire model"), { target: { value: "Pro One" } });
    fireEvent.change(screen.getByLabelText("Tire size"), { target: { value: "700x30" } });
    fireEvent.change(screen.getByLabelText("Tire initial distance (km)"), {
      target: { value: "120" },
    });
    fireEvent.change(screen.getByLabelText("Tire maximum distance (km)"), {
      target: { value: "4000" },
    });
    fireEvent.change(screen.getByLabelText("Tire comments"), {
      target: { value: "Tubeless" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Fit front tire" }));
    expect(mocks.createTire).toHaveBeenCalledWith(
      {
        brand: "Schwalbe",
        model: "Pro One",
        size: "700x30",
        comments: "Tubeless",
        initial_distance_km: 120,
        max_distance_km: 4000,
        bike_id: "b1",
        position: "front",
      },
      expect.anything(),
    );
  });

  it("replaces an installed tire through the same form", () => {
    given([bike()]);
    render(<BikesSection />);
    fireEvent.click(screen.getByRole("button", { name: "Replace" }));
    expect(screen.getByRole("heading", { name: "Replace the front tire" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Tire brand"), { target: { value: "Vittoria" } });
    fireEvent.change(screen.getByLabelText("Tire model"), { target: { value: "Corsa" } });
    fireEvent.click(screen.getByRole("button", { name: "Fit front tire" }));
    expect(mocks.createTire).toHaveBeenCalledWith(
      expect.objectContaining({ bike_id: "b1", position: "front", brand: "Vittoria" }),
      expect.anything(),
    );
  });

  it("takes a tire off or retires it", () => {
    given([bike()]);
    render(<BikesSection />);
    fireEvent.click(screen.getByRole("button", { name: "Take off" }));
    expect(mocks.removeTire).toHaveBeenCalledWith("t1");
    fireEvent.click(screen.getByRole("button", { name: "Retire tire" }));
    expect(mocks.retireTire).toHaveBeenCalledWith("t1");
  });

  it("lists spare tires and fits one to a bike slot, front or rear", () => {
    const spare = tire({
      id: "t2",
      model: "Spare",
      bike_id: null,
      bike_name: null,
      position: null,
    });
    given([bike({ front_tire: null })], [spare]);
    render(<BikesSection />);
    const spares = screen.getByRole("heading", { name: "Spare tires" }).closest("section")!;
    expect(within(spares).getByText("Continental Spare")).toBeInTheDocument();
    fireEvent.change(within(spares).getByLabelText("Fit Continental Spare to a bike"), {
      target: { value: "b1|rear" },
    });
    expect(mocks.installTire).toHaveBeenCalledWith({
      tireId: "t2",
      bikeId: "b1",
      position: "rear",
    });
  });

  it("an empty slot can be filled from a spare", () => {
    const spare = tire({
      id: "t2",
      model: "Spare",
      bike_id: null,
      bike_name: null,
      position: null,
    });
    given([bike({ front_tire: null })], [spare]);
    render(<BikesSection />);
    fireEvent.change(screen.getByLabelText("Fit a spare front tire to Canyon Endurace"), {
      target: { value: "t2" },
    });
    expect(mocks.installTire).toHaveBeenCalledWith({
      tireId: "t2",
      bikeId: "b1",
      position: "front",
    });
  });

  it("adds a bike with its details and the categories it is the default for", () => {
    given([]);
    mocks.createBike.mockImplementation((_body, options) => options.onSuccess({ id: "new-bike" }));
    render(<BikesSection />);
    expect(screen.getByText("No bikes added yet")).toBeInTheDocument();
    expect(screen.queryByLabelText("Bike brand")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Add a bike" }));
    fireEvent.change(screen.getByLabelText("Bike brand"), { target: { value: "Trek" } });
    fireEvent.change(screen.getByLabelText("Bike model"), { target: { value: "Domane" } });
    fireEvent.change(screen.getByLabelText("Bike year"), { target: { value: "2021" } });
    fireEvent.change(screen.getByLabelText("Bike size"), { target: { value: "56" } });
    fireEvent.change(screen.getByLabelText("Bike initial distance (km)"), {
      target: { value: "800" },
    });
    const addForm = screen.getByRole("dialog", { name: "Add a bike" });
    fireEvent.click(within(addForm).getByLabelText("Gravel bike"));
    fireEvent.click(within(addForm).getByLabelText("Bike commute"));
    fireEvent.click(screen.getByRole("button", { name: "Add bike" }));
    expect(mocks.createBike).toHaveBeenCalledWith(
      {
        brand: "Trek",
        model: "Domane",
        year: 2021,
        size: "56",
        comments: null,
        initial_distance_km: 800,
      },
      expect.anything(),
    );
    expect(mocks.setDefault).toHaveBeenCalledWith({ category: "gravel", bikeId: "new-bike" });
    expect(mocks.setDefault).toHaveBeenCalledWith({ category: "commute", bikeId: "new-bike" });
  });

  it("retires a bike, and a retired bike has no controls", () => {
    given([bike()]);
    const { unmount } = render(<BikesSection />);
    fireEvent.click(screen.getByRole("button", { name: "Retire bike" }));
    expect(mocks.retireBike).toHaveBeenCalledWith("b1");
    unmount();

    given([bike({ retired: true, front_tire: null })]);
    render(<BikesSection />);
    expect(screen.getByText("Retired")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retire bike" })).not.toBeInTheDocument();
    expect(screen.queryByText("No front tire")).not.toBeInTheDocument();
  });

  it("opens the add-bike form as a popup on request, and Cancel closes it", () => {
    given([bike()]);
    render(<BikesSection />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Add a bike" }));
    expect(screen.getByRole("dialog", { name: "Add a bike" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
