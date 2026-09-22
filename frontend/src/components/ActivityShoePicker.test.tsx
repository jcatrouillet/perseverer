import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityShoePicker } from "./ActivityShoePicker";

const mockSetShoe = vi.fn();
const mockUseActivityShoe = vi.fn();

vi.mock("../api/queries", () => ({
  useShoes: () => ({
    data: [
      { id: "shoe-1", brand: "Brooks", model: "Catamount" },
      { id: "shoe-2", brand: "Hoka", model: "Speedgoat" },
    ],
    isLoading: false,
  }),
  useActivityShoe: (...args: unknown[]) => mockUseActivityShoe(...args),
  useSetActivityShoe: () => ({ mutate: mockSetShoe, isPending: false, isError: false }),
}));

describe("ActivityShoePicker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseActivityShoe.mockReturnValue({ data: { shoe_id: null }, isLoading: false });
  });

  it("keeps an activity with no assigned shoes quiet while offering an add action", () => {
    render(<ActivityShoePicker activityId="activity-1" hasDistance />);

    expect(screen.queryByText(/no pair selected/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Pair")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument();
  });

  it("opens the optional selector and saves the chosen pair", () => {
    render(<ActivityShoePicker activityId="activity-1" hasDistance />);

    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.change(screen.getByLabelText("Shoes"), { target: { value: "shoe-1" } });

    expect(mockSetShoe).toHaveBeenCalledWith("shoe-1", expect.any(Object));
  });

  it("shows the assigned pair and lets the athlete replace it", () => {
    mockUseActivityShoe.mockReturnValue({ data: { shoe_id: "shoe-1" }, isLoading: false });
    render(<ActivityShoePicker activityId="activity-1" hasDistance />);

    expect(screen.getByLabelText("Activity shoes")).toHaveTextContent("Shoes: Brooks Catamount");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Shoes"), { target: { value: "shoe-2" } });

    expect(mockSetShoe).toHaveBeenCalledWith("shoe-2", expect.any(Object));
  });
});
