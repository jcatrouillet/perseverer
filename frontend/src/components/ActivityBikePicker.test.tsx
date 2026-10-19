import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityBikePicker } from "./ActivityBikePicker";

const mockUseBikes = vi.fn();
const mockUseActivityBike = vi.fn();
const mockSetMutate = vi.fn();

vi.mock("../api/queries", () => ({
  useBikes: () => mockUseBikes(),
  useActivityBike: (...args: unknown[]) => mockUseActivityBike(...args),
  useSetActivityBike: () => ({ mutate: mockSetMutate, isPending: false, isError: false }),
}));

const BIKES = [
  { id: "b1", brand: "Canyon", model: "Endurace" },
  { id: "b2", brand: "Specialized", model: "Sirrus" },
];

describe("ActivityBikePicker", () => {
  beforeEach(() => {
    mockSetMutate.mockClear();
    mockUseBikes.mockReturnValue({ data: BIKES, isLoading: false });
    mockUseActivityBike.mockReturnValue({
      data: { bike_id: "b1", is_default: true },
      isLoading: false,
    });
  });

  it("shows the ride's bike and lets you pick another", () => {
    render(<ActivityBikePicker activityId="a1" isRide />);
    expect(screen.getByText(/Bike: Canyon Endurace/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Bike"), { target: { value: "b2" } });
    expect(mockSetMutate).toHaveBeenCalledWith("b2", expect.anything());
  });

  it("can go back to the category default", () => {
    mockUseActivityBike.mockReturnValue({
      data: { bike_id: "b2", is_default: false },
      isLoading: false,
    });
    render(<ActivityBikePicker activityId="a1" isRide />);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Bike"), { target: { value: "" } });
    expect(mockSetMutate).toHaveBeenCalledWith(null, expect.anything());
  });

  it("offers to add a bike when the ride has none yet", () => {
    mockUseActivityBike.mockReturnValue({ data: { bike_id: null, is_default: false } });
    render(<ActivityBikePicker activityId="a1" isRide />);
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument();
  });

  it("is absent for anything but a ride, and for someone with no bikes at all", () => {
    const { container, rerender } = render(<ActivityBikePicker activityId="a1" isRide={false} />);
    expect(container).toBeEmptyDOMElement();
    mockUseBikes.mockReturnValue({ data: [], isLoading: false });
    mockUseActivityBike.mockReturnValue({ data: { bike_id: null, is_default: false } });
    rerender(<ActivityBikePicker activityId="a1" isRide />);
    expect(container).toBeEmptyDOMElement();
  });
});
