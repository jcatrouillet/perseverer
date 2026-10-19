import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GearPage } from "./GearPage";

// The bikes half has its own tests (BikesSection.test.tsx).
vi.mock("../components/BikesSection", () => ({ BikesSection: () => null }));

const mockCreateMutate = vi.fn();
const mockSetDefaultMutate = vi.fn();
const mockRetireMutate = vi.fn();
const mockUseShoes = vi.fn((includeRetired: boolean) => ({
  data: [],
  isLoading: false,
  isError: false,
  includeRetired,
}));

vi.mock("../api/queries", () => ({
  useAllActivities: () => ({
    data: [
      { id: "run", sport: "running", distance_m: 5000 },
      { id: "walk", sport: "walking", distance_m: 2000 },
    ],
  }),
  useCreateShoe: () => ({
    mutate: mockCreateMutate,
    isPending: false,
    isError: false,
  }),
  useSetDefaultShoe: () => ({ mutate: mockSetDefaultMutate, isPending: false }),
  useRetireShoe: () => ({ mutate: mockRetireMutate, isPending: false }),
  useShoes: (includeRetired: boolean) => mockUseShoes(includeRetired),
}));

describe("GearPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("accepts the default 800 km maximum and submits it as a number", () => {
    render(<GearPage />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add shoes" }));
    fireEvent.change(screen.getByLabelText("Brand"), { target: { value: "Brooks" } });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "Catamount" } });

    const maximum = screen.getByLabelText(/Maximum distance/);
    expect(maximum).toHaveValue(800);
    expect(maximum).toBeValid();
    fireEvent.click(screen.getByRole("button", { name: "Add shoes" }));

    expect(mockCreateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ max_distance_km: 800 }),
      expect.any(Object),
    );
  });

  it("sets a newly-created pair as the default for every selected sport", () => {
    render(<GearPage />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add shoes" }));
    fireEvent.change(screen.getByLabelText("Brand"), { target: { value: "Brooks" } });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "Catamount" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /running/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /walking/i }));
    fireEvent.click(screen.getByRole("button", { name: "Add shoes" }));

    const options = mockCreateMutate.mock.calls[0][1];
    options.onSuccess({ id: "shoe-1" });

    expect(mockSetDefaultMutate).toHaveBeenCalledTimes(2);
    expect(mockSetDefaultMutate).toHaveBeenCalledWith({ sport: "running", shoeId: "shoe-1" });
    expect(mockSetDefaultMutate).toHaveBeenCalledWith({ sport: "walking", shoeId: "shoe-1" });
  });

  it("requests retired shoes only after the user enables the filter", () => {
    render(<GearPage />);
    expect(mockUseShoes).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByLabelText("Show retired"));
    expect(mockUseShoes).toHaveBeenLastCalledWith(true);
  });

  it("keeps the add form in a popup that opens on request and closes again", () => {
    render(<GearPage />);
    expect(screen.queryByLabelText("Brand")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Add shoes" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Add shoes" }));
    expect(screen.getByLabelText("Brand")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Add shoes" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Brand")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Add shoes" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes the form once the pair is added", () => {
    render(<GearPage />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add shoes" }));
    fireEvent.change(screen.getByLabelText("Brand"), { target: { value: "Brooks" } });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "Catamount" } });
    fireEvent.click(screen.getByRole("button", { name: "Add shoes" }));
    act(() => mockCreateMutate.mock.calls[0][1].onSuccess({ id: "shoe-1" }));
    expect(screen.queryByLabelText("Brand")).not.toBeInTheDocument();
  });

  it("shows the shoe inventory and the sport defaults, with the add form in its own popup when open", () => {
    const { unmount } = render(<GearPage />);
    expect(
      screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent),
    ).toEqual(["Your shoes", "Default shoes by sport"]);
    unmount();
    render(<GearPage />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add shoes" }));
    expect(
      screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent),
    ).toEqual(["Your shoes", "Default shoes by sport", "Add shoes"]);
  });
});
