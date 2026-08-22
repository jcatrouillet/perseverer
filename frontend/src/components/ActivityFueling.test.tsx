import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ActivityFueling } from "./ActivityFueling";

describe("ActivityFueling", () => {
  it("shows a prompt to add data when nothing has been logged", () => {
    render(
      <ActivityFueling
        carbohydratesG={null}
        sodiumMg={null}
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={false}
      />,
    );
    expect(screen.getByText("Nothing logged")).toBeInTheDocument();
    expect(screen.getByText("Add fueling data")).toBeInTheDocument();
  });

  it("shows the logged values and an Edit link when data exists", () => {
    render(
      <ActivityFueling
        carbohydratesG={60}
        sodiumMg={500}
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={false}
      />,
    );
    expect(screen.getByText("60g carbs")).toBeInTheDocument();
    expect(screen.getByText("500mg sodium")).toBeInTheDocument();
    expect(screen.getByText("Edit")).toBeInTheDocument();
  });

  it("reveals two number inputs defaulted to the current values when clicked", () => {
    render(
      <ActivityFueling
        carbohydratesG={60}
        sodiumMg={500}
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Edit"));
    expect(screen.getByLabelText("Carbohydrates in grams")).toHaveValue(60);
    expect(screen.getByLabelText("Sodium in milligrams")).toHaveValue(500);
  });

  it("defaults to empty inputs when nothing has been logged yet", () => {
    render(
      <ActivityFueling
        carbohydratesG={null}
        sodiumMg={null}
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Add fueling data"));
    expect(screen.getByLabelText("Carbohydrates in grams")).toHaveValue(null);
    expect(screen.getByLabelText("Sodium in milligrams")).toHaveValue(null);
  });

  it("calls onSubmit with both numeric values on save", () => {
    const onSubmit = vi.fn();
    render(
      <ActivityFueling
        carbohydratesG={null}
        sodiumMg={null}
        onSubmit={onSubmit}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Add fueling data"));
    fireEvent.change(screen.getByLabelText("Carbohydrates in grams"), {
      target: { value: "60" },
    });
    fireEvent.change(screen.getByLabelText("Sodium in milligrams"), {
      target: { value: "500" },
    });
    fireEvent.click(screen.getByText("Save"));
    expect(onSubmit).toHaveBeenCalledWith({ carbohydrates_g: 60, sodium_mg: 500 });
  });

  it("submits null for a field left blank", () => {
    const onSubmit = vi.fn();
    render(
      <ActivityFueling
        carbohydratesG={null}
        sodiumMg={null}
        onSubmit={onSubmit}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Add fueling data"));
    fireEvent.change(screen.getByLabelText("Carbohydrates in grams"), {
      target: { value: "60" },
    });
    fireEvent.click(screen.getByText("Save"));
    expect(onSubmit).toHaveBeenCalledWith({ carbohydrates_g: 60, sodium_mg: null });
  });

  it("cancel dismisses the form without calling onSubmit", () => {
    const onSubmit = vi.fn();
    render(
      <ActivityFueling
        carbohydratesG={null}
        sodiumMg={null}
        onSubmit={onSubmit}
        isSubmitting={false}
        isError={false}
      />,
    );
    fireEvent.click(screen.getByText("Add fueling data"));
    fireEvent.click(screen.getByText("Cancel"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Carbohydrates in grams")).not.toBeInTheDocument();
  });

  it("shows an error message when the save failed", () => {
    render(
      <ActivityFueling
        carbohydratesG={null}
        sodiumMg={null}
        onSubmit={vi.fn()}
        isSubmitting={false}
        isError={true}
      />,
    );
    fireEvent.click(screen.getByText("Add fueling data"));
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save");
  });
});
