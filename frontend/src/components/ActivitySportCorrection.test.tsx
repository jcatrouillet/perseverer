import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ActivitySportCorrection } from "./ActivitySportCorrection";

describe("ActivitySportCorrection", () => {
  it("shows only the trigger link until clicked", () => {
    render(
      <ActivitySportCorrection
        currentSport="running"
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={false}
      />,
    );
    expect(screen.getByText("Not the right sport? Fix it")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("reveals a sport picker defaulted to the current sport when clicked", () => {
    render(
      <ActivitySportCorrection
        currentSport="running"
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Not the right sport? Fix it"));
    expect(screen.getByRole("combobox")).toHaveValue("running");
  });

  it("calls onSubmit with the newly selected sport on save", () => {
    const onSubmit = vi.fn();
    render(
      <ActivitySportCorrection
        currentSport="running"
        onSubmit={onSubmit}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Not the right sport? Fix it"));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "hiking" } });
    fireEvent.click(screen.getByText("Save"));
    expect(onSubmit).toHaveBeenCalledWith("hiking");
  });

  it("cancel dismisses the picker without calling onSubmit", () => {
    const onSubmit = vi.fn();
    render(
      <ActivitySportCorrection
        currentSport="running"
        onSubmit={onSubmit}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Not the right sport? Fix it"));
    fireEvent.click(screen.getByText("Cancel"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("shows an error message when the save failed", () => {
    render(
      <ActivitySportCorrection
        currentSport="running"
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={true}
      />,
    );
    fireEvent.click(screen.getByText("Not the right sport? Fix it"));
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save");
  });
});
