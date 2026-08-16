import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ActivityNameCorrection } from "./ActivityNameCorrection";

describe("ActivityNameCorrection", () => {
  it("shows only the trigger link until clicked", () => {
    render(
      <ActivityNameCorrection
        currentName="Run"
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={false}
      />,
    );
    expect(screen.getByText("Not the right title? Fix it")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("reveals a text input defaulted to the current name when clicked", () => {
    render(
      <ActivityNameCorrection
        currentName="Run"
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Not the right title? Fix it"));
    expect(screen.getByRole("textbox")).toHaveValue("Run");
  });

  it("defaults to an empty input when the activity has no name", () => {
    render(
      <ActivityNameCorrection
        currentName={null}
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Not the right title? Fix it"));
    expect(screen.getByRole("textbox")).toHaveValue("");
  });

  it("calls onSubmit with the trimmed new title on save", () => {
    const onSubmit = vi.fn();
    render(
      <ActivityNameCorrection
        currentName="Run"
        onSubmit={onSubmit}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Not the right title? Fix it"));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "  Santa Clara - Race Pace Run  " },
    });
    fireEvent.click(screen.getByText("Save"));
    expect(onSubmit).toHaveBeenCalledWith("Santa Clara - Race Pace Run");
  });

  it("disables Save when the input is blank", () => {
    render(
      <ActivityNameCorrection
        currentName="Run"
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Not the right title? Fix it"));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "   " } });
    expect(screen.getByText("Save")).toBeDisabled();
  });

  it("cancel dismisses the input without calling onSubmit", () => {
    const onSubmit = vi.fn();
    render(
      <ActivityNameCorrection
        currentName="Run"
        onSubmit={onSubmit}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Not the right title? Fix it"));
    fireEvent.click(screen.getByText("Cancel"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows an error message when the save failed", () => {
    render(
      <ActivityNameCorrection
        currentName="Run"
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={true}
      />,
    );
    fireEvent.click(screen.getByText("Not the right title? Fix it"));
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save");
  });
});
