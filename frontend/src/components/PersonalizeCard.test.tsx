import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PersonalizeSettingsOut } from "../api/types";
import { PersonalizeCard } from "./PersonalizeCard";

const mockSetMutate = vi.fn();

let settingsState: { data?: PersonalizeSettingsOut; isLoading: boolean; isSuccess: boolean } = {
  data: undefined,
  isLoading: false,
  isSuccess: false,
};

vi.mock("../api/queries", () => ({
  usePersonalizeSettings: () => settingsState,
  useSetPersonalizeSettings: () => ({
    mutate: mockSetMutate,
    isPending: false,
    isError: false,
    isSuccess: false,
  }),
}));

function settings(overrides: Partial<PersonalizeSettingsOut> = {}): PersonalizeSettingsOut {
  return {
    week_start_day: "monday",
    time_format: "24h",
    default_view: "week",
    unit_preference: "metric",
    ...overrides,
  };
}

beforeEach(() => {
  settingsState = { data: settings(), isLoading: false, isSuccess: true };
  mockSetMutate.mockClear();
});

describe("PersonalizeCard", () => {
  it("reflects the stored settings in the form controls", () => {
    settingsState = {
      data: settings({ week_start_day: "sunday", time_format: "12h", unit_preference: "imperial" }),
      isLoading: false,
      isSuccess: true,
    };
    render(<PersonalizeCard />);
    expect(screen.getByLabelText("Sunday")).toBeChecked();
    expect(screen.getByLabelText("Monday")).not.toBeChecked();
    expect(screen.getByLabelText("12-hour (AM/PM)")).toBeChecked();
    expect(screen.getByLabelText("Miles")).toBeChecked();
    expect(screen.getByLabelText("Starting page")).toHaveValue("week");
  });

  it("saves the selected values", () => {
    render(<PersonalizeCard />);
    fireEvent.click(screen.getByLabelText("Sunday"));
    fireEvent.click(screen.getByLabelText("12-hour (AM/PM)"));
    fireEvent.click(screen.getByLabelText("Miles"));
    fireEvent.change(screen.getByLabelText("Starting page"), { target: { value: "month" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockSetMutate).toHaveBeenCalledWith({
      week_start_day: "sunday",
      time_format: "12h",
      default_view: "month",
      unit_preference: "imperial",
    });
  });

  it("offers the last imported activity as a starting page", () => {
    settingsState = {
      data: settings({ default_view: "last_activity" }),
      isLoading: false,
      isSuccess: true,
    };
    render(<PersonalizeCard />);
    const select = screen.getByLabelText("Starting page");
    expect(select).toHaveValue("last_activity");
    expect(screen.getByRole("option", { name: "Last activity imported" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mockSetMutate).toHaveBeenCalledWith(
      expect.objectContaining({ default_view: "last_activity" }),
    );
  });

  it("defaults to Monday/24h/Week/km before any settings have loaded", () => {
    settingsState = { data: undefined, isLoading: true, isSuccess: false };
    render(<PersonalizeCard />);
    expect(screen.getByLabelText("Monday")).toBeChecked();
    expect(screen.getByLabelText("24-hour")).toBeChecked();
    expect(screen.getByLabelText("Kilometers")).toBeChecked();
    expect(screen.getByLabelText("Starting page")).toHaveValue("week");
  });

  it("switches the color theme immediately, stores it on this device, and does not save it to the account", () => {
    localStorage.removeItem("perseverer.theme");
    render(<PersonalizeCard />);
    // With nothing stored the card shows the OS default (dark here), so start from the other one.
    fireEvent.click(screen.getByLabelText("Light"));
    expect(screen.getByLabelText("Light")).toBeChecked();
    expect(localStorage.getItem("perseverer.theme")).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    fireEvent.click(screen.getByLabelText("Dark"));
    expect(screen.getByLabelText("Dark")).toBeChecked();
    expect(localStorage.getItem("perseverer.theme")).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(mockSetMutate).not.toHaveBeenCalled();
  });

  it("shows the stored theme as selected", () => {
    localStorage.setItem("perseverer.theme", "light");
    render(<PersonalizeCard />);
    expect(screen.getByLabelText("Light")).toBeChecked();
    expect(screen.getByLabelText("Dark")).not.toBeChecked();
    localStorage.removeItem("perseverer.theme");
  });
});
