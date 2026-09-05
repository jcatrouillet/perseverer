import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { PlannedWorkoutOut } from "../api/types";
import { preloadExerciseCatalog } from "./ExerciseStepEditor";
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
      duration_reps: null,
      exercise_category: null,
      exercise_name: null,
      weight_kg: null,
    },
  ],
  parse_errors: [],
  push_status: "draft",
  push_error: null,
  garmin_workout_id: null,
  garmin_scheduled_at: null,
};

describe("ScheduleWorkoutForm", () => {
  beforeAll(async () => {
    await preloadExerciseCatalog();
  });

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
        steps: null,
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
        steps: null,
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

  it("Copy writes the full scheduled workout to the clipboard, not just name/source_text", () => {
    mockUsePlannedWorkout.mockReturnValue({
      data: {
        ...SCHEDULED,
        sport: "yoga",
        name: "Evening yoga",
        source_text: null,
        scheduled_time: "18:30",
        estimated_duration_s: 2700,
        steps: [],
      },
      isLoading: false,
      isError: false,
    });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Copy"));

    const stored = JSON.parse(localStorage.getItem("perseverer_workout_clipboard") ?? "{}");
    expect(stored).toEqual(
      expect.objectContaining({
        sport: "yoga",
        name: "Evening yoga",
        scheduled_time: "18:30",
        duration_minutes: 45,
      }),
    );
  });

  it("hides Push to Garmin for a sport with no push builder (e.g. legacy 'fitness' data)", () => {
    // "fitness" is no longer offered in the sport dropdown at all, but a workout saved under it
    // before that removal must still degrade gracefully rather than offering a push that would
    // just fail.
    mockUsePlannedWorkout.mockReturnValue({
      data: { ...SCHEDULED, sport: "fitness" },
      isLoading: false,
      isError: false,
    });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.queryByText("Push to Garmin")).not.toBeInTheDocument();
  });

  it("no longer offers fitness as a sport option", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    expect(screen.queryByRole("option", { name: "Fitness" })).not.toBeInTheDocument();
  });

  describe("hiit/strength_training exercise picker", () => {
    it("switching to strength_training shows the exercise picker instead of the textarea", () => {
      mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
      render(<ScheduleWorkoutForm localDate="2026-09-01" />);
      fireEvent.click(screen.getByText("Schedule a workout"));

      fireEvent.change(screen.getByRole("combobox"), { target: { value: "strength_training" } });

      expect(screen.queryByPlaceholderText(/Warmup 10m/)).not.toBeInTheDocument();
      expect(screen.queryByText("Duration (minutes)")).not.toBeInTheDocument();
      expect(screen.getByText("+ Add exercise")).toBeInTheDocument();
      expect(screen.getByText("+ Add rest")).toBeInTheDocument();
    });

    it("picking a real exercise and saving sends a structured step", () => {
      mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
      render(<ScheduleWorkoutForm localDate="2026-09-01" />);
      fireEvent.click(screen.getByText("Schedule a workout"));
      fireEvent.change(screen.getByRole("combobox"), { target: { value: "strength_training" } });

      fireEvent.click(screen.getByText("+ Add exercise"));
      fireEvent.change(screen.getByPlaceholderText(/Search exercises/), {
        target: { value: "Bench Press" },
      });
      fireEvent.mouseDown(screen.getByRole("button", { name: /^Bench Press/ }));

      fireEvent.click(screen.getByText("Save"));

      expect(mockSave).toHaveBeenCalledWith(
        expect.objectContaining({
          sport: "strength_training",
          source_text: null,
          steps: [
            expect.objectContaining({
              step_index: 0,
              duration_type: "reps",
              duration_reps: 10,
              intensity: "active",
              exercise_category: "BENCH_PRESS",
              exercise_name: "",
            }),
          ],
        }),
        expect.anything(),
      );
    });

    it("editing an already-saved hiit workout hydrates the picker from its steps", () => {
      mockUsePlannedWorkout.mockReturnValue({
        data: {
          ...SCHEDULED,
          sport: "hiit",
          source_text: null,
          steps: [
            {
              step_index: 0,
              duration_type: "reps",
              duration_time_s: null,
              duration_distance_m: null,
              target_type: null,
              target_low: null,
              target_high: null,
              target_hr_zone: null,
              cadence_low: null,
              cadence_high: null,
              intensity: "active",
              repeat_from_step: null,
              repeat_count: null,
              duration_reps: 15,
              exercise_category: "TOTAL_BODY",
              exercise_name: "BURPEE",
              weight_kg: null,
            },
          ],
        },
        isLoading: false,
        isError: false,
      });
      render(<ScheduleWorkoutForm localDate="2026-09-01" />);
      fireEvent.click(screen.getByText("Edit"));

      expect((screen.getByPlaceholderText(/Search exercises/) as HTMLInputElement).value).toBe(
        "Burpee",
      );
      expect((screen.getByDisplayValue("15") as HTMLInputElement)).toBeInTheDocument();
    });

    it("building a set of several exercises with rest, repeated, sends one repeat marker after them", () => {
      mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
      render(<ScheduleWorkoutForm localDate="2026-09-01" />);
      fireEvent.click(screen.getByText("Schedule a workout"));
      fireEvent.change(screen.getByRole("combobox"), { target: { value: "strength_training" } });

      fireEvent.click(screen.getByText("+ Add a set of exercises"));
      fireEvent.click(screen.getByText("+ Add exercise to set"));
      fireEvent.change(screen.getByPlaceholderText(/Search exercises/), {
        target: { value: "Bench Press" },
      });
      fireEvent.mouseDown(screen.getByRole("button", { name: /^Bench Press/ }));
      fireEvent.click(screen.getByText("+ Add rest to set"));

      const repeatInput = screen.getByPlaceholderText("e.g. 3");
      fireEvent.change(repeatInput, { target: { value: "4" } });

      fireEvent.click(screen.getByText("Save"));

      expect(mockSave).toHaveBeenCalledWith(
        expect.objectContaining({
          sport: "strength_training",
          steps: [
            expect.objectContaining({ step_index: 0, exercise_category: "BENCH_PRESS" }),
            expect.objectContaining({ step_index: 1, intensity: "rest" }),
            expect.objectContaining({
              step_index: 2,
              duration_type: "repeat_until_steps_cmplt",
              repeat_from_step: 0,
              repeat_count: 4,
            }),
          ],
        }),
        expect.anything(),
      );
    });

    it("supports a standalone exercise alongside a separate set", () => {
      mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
      render(<ScheduleWorkoutForm localDate="2026-09-01" />);
      fireEvent.click(screen.getByText("Schedule a workout"));
      fireEvent.change(screen.getByRole("combobox"), { target: { value: "hiit" } });

      // A standalone warmup exercise, outside any set.
      fireEvent.click(screen.getByText("+ Add exercise"));
      fireEvent.change(screen.getByPlaceholderText(/Search exercises/), {
        target: { value: "Burpee" },
      });
      fireEvent.mouseDown(screen.getByRole("button", { name: "BurpeeTotal Body" }));

      // A separate 3x set with its own exercise.
      fireEvent.click(screen.getByText("+ Add a set of exercises"));
      fireEvent.click(screen.getByText("+ Add exercise to set"));
      const searchBoxes = screen.getAllByPlaceholderText(/Search exercises/);
      fireEvent.change(searchBoxes[searchBoxes.length - 1], { target: { value: "Air Squat" } });
      fireEvent.mouseDown(screen.getByRole("button", { name: "Air SquatSquat" }));
      fireEvent.change(screen.getByPlaceholderText("e.g. 3"), { target: { value: "3" } });

      fireEvent.click(screen.getByText("Save"));

      expect(mockSave).toHaveBeenCalledWith(
        expect.objectContaining({
          steps: [
            expect.objectContaining({ step_index: 0, exercise_category: "TOTAL_BODY" }),
            expect.objectContaining({ step_index: 1, exercise_category: "SQUAT" }),
            expect.objectContaining({
              step_index: 2,
              duration_type: "repeat_until_steps_cmplt",
              repeat_from_step: 1,
              repeat_count: 3,
            }),
          ],
        }),
        expect.anything(),
      );
    });

    it("Remove set deletes the whole set, not just one exercise inside it", () => {
      mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
      render(<ScheduleWorkoutForm localDate="2026-09-01" />);
      fireEvent.click(screen.getByText("Schedule a workout"));
      fireEvent.change(screen.getByRole("combobox"), { target: { value: "hiit" } });

      fireEvent.click(screen.getByText("+ Add a set of exercises"));
      fireEvent.click(screen.getByText("+ Add exercise to set"));
      expect(screen.getByText("+ Add exercise to set")).toBeInTheDocument();

      fireEvent.click(screen.getByText("Remove set"));

      expect(screen.queryByText("+ Add exercise to set")).not.toBeInTheDocument();
    });
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

  it("pasting a copied yoga workout restores duration and time of day, not just name", () => {
    localStorage.setItem(
      "perseverer_workout_clipboard",
      JSON.stringify({
        sport: "yoga",
        name: "Copied yoga",
        source_text: null,
        scheduled_time: "07:00",
        duration_minutes: 60,
      }),
    );
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Paste copied workout"));

    expect((screen.getByPlaceholderText("e.g. Evening yoga") as HTMLInputElement).value).toBe(
      "Copied yoga",
    );
    expect((screen.getByLabelText("Duration (minutes)") as HTMLInputElement).value).toBe("60");
    expect((screen.getByLabelText("Time of day") as HTMLInputElement).value).toBe("07:00");
  });

  it("pasting a copied strength_training workout restores its exercise steps", () => {
    localStorage.setItem(
      "perseverer_workout_clipboard",
      JSON.stringify({
        sport: "strength_training",
        name: "Copied lift",
        source_text: null,
        steps: [
          {
            step_index: 0,
            duration_type: "reps",
            duration_time_s: null,
            duration_distance_m: null,
            target_type: null,
            target_low: null,
            target_high: null,
            target_hr_zone: null,
            cadence_low: null,
            cadence_high: null,
            intensity: "active",
            repeat_from_step: null,
            repeat_count: null,
            duration_reps: 8,
            exercise_category: "BENCH_PRESS",
            exercise_name: "",
            weight_kg: 55,
          },
        ],
      }),
    );
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Paste copied workout"));

    expect((screen.getByPlaceholderText(/Search exercises/) as HTMLInputElement).value).toBe(
      "Bench Press",
    );
    expect(screen.getByDisplayValue("8")).toBeInTheDocument();
    expect(screen.getByDisplayValue("55")).toBeInTheDocument();
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

  it("Repeat this schedule also carries exercise steps for hiit/strength_training", () => {
    mockUsePlannedWorkout.mockReturnValue({ data: NONE, isLoading: false, isError: false });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "strength_training" } });
    fireEvent.click(screen.getByText("+ Add exercise"));
    fireEvent.change(screen.getByPlaceholderText(/Search exercises/), {
      target: { value: "Bench Press" },
    });
    fireEvent.mouseDown(screen.getByRole("button", { name: /^Bench Press/ }));

    fireEvent.click(screen.getByText("Repeat this schedule…"));
    fireEvent.click(screen.getByText("Create schedule"));

    expect(mockRecurring).toHaveBeenCalledWith(
      expect.objectContaining({
        local_date: "2026-09-01",
        sport: "strength_training",
        source_text: null,
        steps: [
          expect.objectContaining({
            exercise_category: "BENCH_PRESS",
            exercise_name: "",
          }),
        ],
      }),
      expect.anything(),
    );
  });
});
