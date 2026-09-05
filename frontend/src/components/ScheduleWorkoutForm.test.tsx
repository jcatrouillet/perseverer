import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PlannedWorkoutOut } from "../api/types";
import { ScheduleWorkoutForm } from "./ScheduleWorkoutForm";

const mockUsePlannedWorkout = vi.fn();
const mockSave = vi.fn();
const mockDelete = vi.fn();
const mockPush = vi.fn();
const mockRecurring = vi.fn();

vi.mock("../api/queries", () => ({
  usePlannedWorkout: (...args: unknown[]) => mockUsePlannedWorkout(...args),
  useSavePlannedWorkout: () => ({ mutate: mockSave, isPending: false }),
  useDeletePlannedWorkout: () => ({ mutate: mockDelete, isPending: false }),
  usePushPlannedWorkout: () => ({ mutate: mockPush, isPending: false }),
  useCreateRecurringPlannedWorkouts: () => ({
    mutate: mockRecurring,
    isPending: false,
    data: undefined,
  }),
}));

const NONE: PlannedWorkoutOut = {
  available: false,
  id: null,
  local_date: null,
  sport: null,
  name: null,
  source_text: null,
  scheduled_time: null,
  estimated_duration_s: null,
  steps: [],
  parse_errors: [],
  push_status: null,
  push_error: null,
  garmin_workout_id: null,
  garmin_scheduled_at: null,
};

const SCHEDULED: PlannedWorkoutOut = {
  available: true,
  id: 1,
  local_date: "2026-09-01",
  sport: "running",
  name: "Tempo run",
  source_text: "Warmup 10m",
  scheduled_time: null,
  estimated_duration_s: 600,
  steps: [
    {
      step_index: 0,
      duration_type: "time",
      duration_time_s: 600,
      duration_distance_m: null,
      target_type: null,
      target_low: null,
      target_high: null,
      target_hr_zone: null,
      cadence_low: null,
      cadence_high: null,
      intensity: "warmup",
      repeat_from_step: null,
      repeat_count: null,
    },
  ],
  parse_errors: [],
  push_status: "draft",
  push_error: null,
  garmin_workout_id: null,
  garmin_scheduled_at: null,
};

describe("ScheduleWorkoutForm", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("shows 'Schedule a workout' when nothing is planned for the date", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    expect(screen.getByText("Schedule a workout")).toBeInTheDocument();
  });

  it("clicking 'Schedule a workout' reveals the form with a live parse preview", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Schedule a workout"));
    const textarea = screen.getByPlaceholderText(/Warmup 10m/);
    fireEvent.change(textarea, { target: { value: "Warmup 10m" } });

    expect(screen.getByText("Warmup")).toBeInTheDocument();
    expect(screen.getByText("10m")).toBeInTheDocument();
  });

  it("shows parse errors for a malformed line", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    const textarea = screen.getByPlaceholderText(/Warmup 10m/);
    fireEvent.change(textarea, { target: { value: "10m sparkles" } });

    expect(screen.getByText(/unrecognized token/)).toBeInTheDocument();
  });

  it("Save calls the save mutation with the current form content", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    fireEvent.change(screen.getByPlaceholderText("e.g. Tempo run"), {
      target: { value: "Easy jog" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Warmup 10m/), {
      target: { value: "Warmup 10m" },
    });
    fireEvent.click(screen.getByText("Save"));

    expect(mockSave).toHaveBeenCalledWith(
      {
        localDate: "2026-09-01",
        sport: "running",
        name: "Easy jog",
        source_text: "Warmup 10m",
        scheduled_time: null,
        duration_minutes: null,
      },
      expect.anything(),
    );
  });

  it("switching to yoga shows duration/time fields instead of the syntax textarea", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "yoga" } });

    expect(screen.getByText("Duration (minutes)")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Warmup 10m/)).not.toBeInTheDocument();
    expect(screen.queryByText("+ Add step")).not.toBeInTheDocument();
  });

  it("Save for yoga sends duration_minutes and scheduled_time, no source_text required", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "yoga" } });

    fireEvent.change(screen.getByPlaceholderText("e.g. Evening yoga"), {
      target: { value: "Evening yoga" },
    });
    fireEvent.change(screen.getByLabelText("Duration (minutes)"), { target: { value: "45" } });
    fireEvent.change(screen.getByLabelText("Time of day"), { target: { value: "18:30" } });
    fireEvent.click(screen.getByText("Save"));

    expect(mockSave).toHaveBeenCalledWith(
      {
        localDate: "2026-09-01",
        sport: "yoga",
        name: "Evening yoga",
        source_text: null,
        scheduled_time: "18:30",
        duration_minutes: 45,
      },
      expect.anything(),
    );
  });

  it("shows the push status and a Push/Delete action once a workout is scheduled", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: SCHEDULED, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText("Tempo run")).toBeInTheDocument();
    expect(screen.getByText("Draft")).toBeInTheDocument();
    expect(screen.getByText("Push to Garmin")).toBeInTheDocument();
  });

  it("Push to Garmin calls the push mutation with the date", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: SCHEDULED, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Push to Garmin"));

    expect(mockPush).toHaveBeenCalledWith("2026-09-01");
  });

  it("shows Push to Garmin for a scheduled yoga workout too", () => {
    mockUsePlannedWorkout.mockReturnValue({
      data: { ...SCHEDULED, sport: "yoga", scheduled_time: "18:30", estimated_duration_s: 2700 },
      isLoading: false,
      isError: false,
    });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText("Push to Garmin")).toBeInTheDocument();
    expect(screen.getByText(/18:30/)).toBeInTheDocument();
    expect(screen.getByText(/45 min/)).toBeInTheDocument();
  });

  it("hides Push to Garmin for fitness (no builder yet)", () => {
    mockUsePlannedWorkout.mockReturnValue({
      data: { ...SCHEDULED, sport: "fitness" },
      isLoading: false,
      isError: false,
    });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.queryByText("Push to Garmin")).not.toBeInTheDocument();
  });

  it("Delete calls the delete mutation with the date", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: SCHEDULED, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Delete"));

    expect(mockDelete).toHaveBeenCalledWith("2026-09-01");
  });

  it("a copied workout in the clipboard offers a Paste action that pre-fills the form", () => {
    localStorage.setItem(
      "perseverer_workout_clipboard",
      JSON.stringify({ sport: "running", name: "Copied run", source_text: "Warmup 5m" }),
    );
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Paste copied workout"));

    expect((screen.getByPlaceholderText("e.g. Tempo run") as HTMLInputElement).value).toBe(
      "Copied run",
    );
    expect((screen.getByPlaceholderText(/Warmup 10m/) as HTMLTextAreaElement).value).toBe(
      "Warmup 5m",
    );
  });

  it("Repeat this schedule reveals the recurrence controls, and creating one calls the mutation", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));
    fireEvent.change(screen.getByPlaceholderText(/Warmup 10m/), {
      target: { value: "Warmup 10m" },
    });

    fireEvent.click(screen.getByText("Repeat this schedule…"));
    expect(screen.getByText("Create schedule")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Create schedule"));

    expect(mockRecurring).toHaveBeenCalledWith(
      expect.objectContaining({
        local_date: "2026-09-01",
        sport: "running",
        frequency: "weekly",
        count: 4,
      }),
      expect.anything(),
    );
  });
});
