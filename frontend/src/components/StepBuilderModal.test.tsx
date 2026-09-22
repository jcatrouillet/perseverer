import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StepBuilderModal } from "./StepBuilderModal";

describe("StepBuilderModal", () => {
  it("generates a plain duration-only line with no target", () => {
    const onGenerate = vi.fn();
    render(<StepBuilderModal open onClose={vi.fn()} onGenerate={onGenerate} />);

    fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "10" } });
    fireEvent.click(screen.getByText("Generate"));

    expect(onGenerate).toHaveBeenCalledWith("10m");
  });

  it("generates a pace-range step with intensity", () => {
    const onGenerate = vi.fn();
    render(<StepBuilderModal open onClose={vi.fn()} onGenerate={onGenerate} />);

    fireEvent.change(screen.getByLabelText("Intensity"), { target: { value: "warmup" } });
    fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "3" } });
    fireEvent.click(screen.getByLabelText("Pace"));
    fireEvent.click(screen.getByText("Range?", { selector: "label" }));
    fireEvent.change(screen.getByPlaceholderText("5:00"), { target: { value: "5:00" } });
    fireEvent.change(screen.getByPlaceholderText("5:20"), { target: { value: "5:20" } });
    fireEvent.click(screen.getByText("Generate"));

    expect(onGenerate).toHaveBeenCalledWith("Warmup 3m 5:00-5:20/km Pace");
  });

  it("generates an HR-zone step", () => {
    const onGenerate = vi.fn();
    render(<StepBuilderModal open onClose={vi.fn()} onGenerate={onGenerate} />);

    fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Time or distance?"), { target: { value: "s" } });
    fireEvent.click(screen.getByLabelText("Heart rate"));
    fireEvent.click(screen.getByLabelText("Zone"));
    fireEvent.change(screen.getByDisplayValue("Z2"), { target: { value: "3" } });
    fireEvent.click(screen.getByText("Generate"));

    expect(onGenerate).toHaveBeenCalledWith("2s Z3 HR");
  });

  it("generates cadence alongside a pace target", () => {
    const onGenerate = vi.fn();
    render(<StepBuilderModal open onClose={vi.fn()} onGenerate={onGenerate} />);

    fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "3" } });
    fireEvent.click(screen.getByLabelText("Pace"));
    fireEvent.change(screen.getByPlaceholderText("5:00"), { target: { value: "5:10" } });
    fireEvent.click(screen.getByLabelText("Cadence"));
    fireEvent.change(screen.getByPlaceholderText("170"), { target: { value: "175" } });
    fireEvent.click(screen.getByText("Generate"));

    expect(onGenerate).toHaveBeenCalledWith("3m 5:10/km Pace 175spm");
  });

  it("building a repeat with two steps generates an Nx block", () => {
    const onGenerate = vi.fn();
    render(<StepBuilderModal open onClose={vi.fn()} onGenerate={onGenerate} />);

    fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Repeat?"), { target: { value: "4" } });
    fireEvent.click(screen.getByText("+ Add another step to this block"));

    const durationInputs = screen.getAllByLabelText("Duration");
    fireEvent.change(durationInputs[1], { target: { value: "2" } });
    fireEvent.click(screen.getByText("Generate"));

    expect(onGenerate).toHaveBeenCalledWith("4x\n3m\n2m");
  });

  it("does nothing when the duration is left blank", () => {
    const onGenerate = vi.fn();
    render(<StepBuilderModal open onClose={vi.fn()} onGenerate={onGenerate} />);
    fireEvent.click(screen.getByText("Generate"));
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it("generates a bare lap-button step with no estimate", () => {
    const onGenerate = vi.fn();
    render(<StepBuilderModal open onClose={vi.fn()} onGenerate={onGenerate} />);

    fireEvent.change(screen.getByLabelText("Duration type"), { target: { value: "lap" } });
    fireEvent.click(screen.getByText("Generate"));

    expect(onGenerate).toHaveBeenCalledWith("lap");
  });

  it("generates a lap-button step with an HR target, and never requires the estimate", () => {
    const onGenerate = vi.fn();
    render(<StepBuilderModal open onClose={vi.fn()} onGenerate={onGenerate} />);

    fireEvent.change(screen.getByLabelText("Duration type"), { target: { value: "lap" } });
    fireEvent.click(screen.getByLabelText("Heart rate"));
    fireEvent.click(screen.getByText("Range?", { selector: "label" }));
    fireEvent.change(screen.getByPlaceholderText("140"), { target: { value: "118" } });
    fireEvent.change(screen.getByPlaceholderText("150"), { target: { value: "128" } });
    fireEvent.click(screen.getByText("Generate"));

    expect(onGenerate).toHaveBeenCalledWith("lap 118-128 HR");
  });

  it("generates a lap-button step with a distance estimate, kept separate from the duration unit", () => {
    const onGenerate = vi.fn();
    render(<StepBuilderModal open onClose={vi.fn()} onGenerate={onGenerate} />);

    fireEvent.change(screen.getByLabelText("Intensity"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Duration type"), { target: { value: "lap" } });
    fireEvent.change(screen.getByLabelText("Estimate (optional)"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Unit"), { target: { value: "km" } });
    fireEvent.click(screen.getByLabelText("Pace"));
    fireEvent.change(screen.getByPlaceholderText("5:00"), { target: { value: "5:45" } });
    fireEvent.click(screen.getByText("Range?", { selector: "label" }));
    fireEvent.change(screen.getByPlaceholderText("5:20"), { target: { value: "6:15" } });
    fireEvent.click(screen.getByText("Generate"));

    expect(onGenerate).toHaveBeenCalledWith("lap 5km 5:45-6:15/km Pace");
  });

  it("switching back to Time or distance drops the lap keyword", () => {
    const onGenerate = vi.fn();
    render(<StepBuilderModal open onClose={vi.fn()} onGenerate={onGenerate} />);

    fireEvent.change(screen.getByLabelText("Duration type"), { target: { value: "lap" } });
    fireEvent.change(screen.getByLabelText("Duration type"), { target: { value: "duration" } });
    fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "10" } });
    fireEvent.click(screen.getByText("Generate"));

    expect(onGenerate).toHaveBeenCalledWith("10m");
  });
});
