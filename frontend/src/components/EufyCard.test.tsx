import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { EufyStatusOut } from "../api/types";
import { EufyCard } from "./EufyCard";

const mockUseEufyStatus = vi.fn();
const mockLoginMutate = vi.fn();

vi.mock("../api/queries", () => ({
  useEufyStatus: () => mockUseEufyStatus(),
  useEufyLogin: () => ({
    mutate: mockLoginMutate,
    isPending: false,
    isError: false,
    isSuccess: false,
  }),
}));

const NOT_CONNECTED: EufyStatusOut = { configured: false, email: null };
const CONNECTED: EufyStatusOut = { configured: true, email: "me@example.com" };

describe("EufyCard", () => {
  it("shows not-connected status", () => {
    mockUseEufyStatus.mockReturnValue({ data: NOT_CONNECTED });
    render(<EufyCard />);
    expect(screen.getByText("Not connected yet.")).toBeInTheDocument();
  });

  it("shows connected status with the configured email", () => {
    mockUseEufyStatus.mockReturnValue({ data: CONNECTED });
    render(<EufyCard />);
    expect(screen.getByText(/Connected — me@example.com/)).toBeInTheDocument();
  });

  it("submits the form with email, password, device id, and customer id", () => {
    mockUseEufyStatus.mockReturnValue({ data: NOT_CONNECTED });
    render(<EufyCard />);

    fireEvent.change(screen.getByLabelText("Eufy account email"), {
      target: { value: "me@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Eufy account password"), {
      target: { value: "hunter2" },
    });
    fireEvent.change(screen.getByLabelText("Device ID"), { target: { value: "dev-1" } });
    fireEvent.change(screen.getByLabelText("Customer ID"), { target: { value: "cust-1" } });
    fireEvent.click(screen.getByText("Save"));

    expect(mockLoginMutate).toHaveBeenCalledWith(
      {
        email: "me@example.com",
        password: "hunter2",
        device_id: "dev-1",
        customer_id: "cust-1",
      },
      expect.anything(),
    );
  });
});
