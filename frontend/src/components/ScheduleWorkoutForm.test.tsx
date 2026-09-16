import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { PlannedWorkoutOut } from "../api/types";
import { preloadExerciseCatalog } from "./ExerciseStepEditor";
import { ScheduleWorkoutForm } from "./ScheduleWorkoutForm";

const mockUsePlannedWorkoutsForDate = vi.fn();
const mockCreate = vi.fn();
const mockUpdate = vi.fn();
const mockDelete = vi.fn();
const mockPush = vi.fn();
const mockComplete = vi.fn();
const mockUncomplete = vi.fn();
const mockRecurring = vi.fn();

vi.mock("../api/queries", () => ({
  usePlannedWorkoutsForDate: (...args: unknown[]) => mockUsePlannedWorkoutsForDate(...args),
  useCreatePlannedWorkout: () => ({ mutate: mockCreate, isPending: false }),
  useUpdatePlannedWorkout: () => ({ mutate: mockUpdate, isPending: false }),
  useDeletePlannedWorkout: () => ({ mutate: mockDelete, isPending: false }),
  usePushPlannedWorkout: () => ({ mutate: mockPush, isPending: false }),
  useCompletePlannedWorkout: () => ({ mutate: mockComplete, isPending: false }),
  useUncompletePlannedWorkout: () => ({ mutate: mockUncomplete, isPending: false }),
  useCreateRecurringPlannedWorkouts: () => ({
    mutate: mockRecurring,
    isPending: false,
    data: undefined,
  }),
}));

const NONE: { data: PlannedWorkoutOut[]; isLoading: boolean; isError: boolean } = {
  data: [],
  isLoading: false,
  isError: false,
};

const SCHEDULED: PlannedWorkoutOut = {
  available: true,
  id: 1,
  local_date: "2026-09-01",
  sport: "running",
  name: "Tempo run",
  source_text: "Warmup 10m",
  scheduled_time: null,
  comment: null,
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
      comment: null,
    },
  ],
  parse_errors: [],
  push_status: "draft",
  push_error: null,
  garmin_workout_id: null,
  garmin_scheduled_at: null,
  completed_at: null,
  matched_activity_id: null,
  estimated_distance_m: null,
  estimated_load: null,
  segments: [],
};

function withOne(workout: PlannedWorkoutOut) {
  return { data: [workout], isLoading: false, isError: false };
}

describe("ScheduleWorkoutForm", () => {
  beforeAll(async () => {
    await preloadExerciseCatalog();
  });

  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("shows 'Schedule a workout' when nothing is planned for the date", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    expect(screen.getByText("Schedule a workout")).toBeInTheDocument();
  });

  it("clicking 'Schedule a workout' reveals the form with a live parse preview", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Schedule a workout"));
    const textarea = screen.getByPlaceholderText(/Warmup 10m/);
    fireEvent.change(textarea, { target: { value: "Warmup 10m" } });

    expect(screen.getByText("Warmup")).toBeInTheDocument();
    expect(screen.getByText("10m")).toBeInTheDocument();
  });

  it("shows parse errors for a malformed line", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    const textarea = screen.getByPlaceholderText(/Warmup 10m/);
    fireEvent.change(textarea, { target: { value: "10m sparkles" } });

    expect(screen.getByText(/unrecognized token/)).toBeInTheDocument();
  });

  it("Save calls the create mutation with the current form content", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    fireEvent.change(screen.getByPlaceholderText("e.g. Tempo run"), {
      target: { value: "Easy jog" },
    });
    fireEvent.change(screen.getByPlaceholderText(/Warmup 10m/), {
      target: { value: "Warmup 10m" },
    });
    fireEvent.click(screen.getByText("Save"));

    expect(mockCreate).toHaveBeenCalledWith(
      {
        localDate: "2026-09-01",
        sport: "running",
        name: "Easy jog",
        source_text: "Warmup 10m",
        scheduled_time: null,
        duration_minutes: null,
        steps: null,
        comment: null,
      },
      expect.anything(),
    );
  });

  it("switching to yoga shows duration/time fields instead of the syntax textarea", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "yoga" } });

    expect(screen.getByText("Duration (minutes)")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Warmup 10m/)).not.toBeInTheDocument();
    expect(screen.queryByText("+ Add step")).not.toBeInTheDocument();
  });

  it("offers a general-guidance comment field for running but not for yoga/bouldering", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    expect(screen.getByText("General guidance (optional)")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "yoga" } });
    expect(screen.queryByText("General guidance (optional)")).not.toBeInTheDocument();
  });

  it("saving includes the general guidance comment", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    fireEvent.change(screen.getByPlaceholderText(/Warmup 10m/), {
      target: { value: "Warmup 10m" },
    });
    fireEvent.change(screen.getByPlaceholderText(/easy effort, focus on cadence/), {
      target: { value: "Legs still sore -- cut it short if needed." },
    });
    fireEvent.click(screen.getByText("Save"));

    expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({ comment: "Legs still sore -- cut it short if needed." }),
      expect.anything(),
    );
  });

  it("shows a hint about the inline comment syntax for running", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    expect(screen.getByText(/comment on that step/)).toBeInTheDocument();
  });

  it("an inline '# comment' on a step line shows up in the live preview", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    fireEvent.change(screen.getByPlaceholderText(/Warmup 10m/), {
      target: { value: "Warmup 10m # legs still sore from Tuesday" },
    });

    // The step preview list, not the textarea itself (whose own rendered value also contains
    // this substring) -- scope to the <li> the preview renders.
    expect(screen.getByRole("listitem")).toHaveTextContent("legs still sore from Tuesday");
  });

  it("Save for yoga sends duration_minutes and scheduled_time, no source_text required", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "yoga" } });

    fireEvent.change(screen.getByPlaceholderText("e.g. Evening yoga"), {
      target: { value: "Evening yoga" },
    });
    fireEvent.change(screen.getByLabelText("Duration (minutes)"), { target: { value: "45" } });
    fireEvent.change(screen.getByLabelText("Hour"), { target: { value: "18" } });
    fireEvent.change(screen.getByLabelText("Minute"), { target: { value: "30" } });
    fireEvent.click(screen.getByText("Save"));

    expect(mockCreate).toHaveBeenCalledWith(
      {
        localDate: "2026-09-01",
        sport: "yoga",
        name: "Evening yoga",
        source_text: null,
        scheduled_time: "18:30",
        duration_minutes: 45,
        steps: null,
        comment: null,
      },
      expect.anything(),
    );
  });

  it("shows the push status and a Push/Delete action once a workout is scheduled", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(withOne(SCHEDULED));
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText("Tempo run")).toBeInTheDocument();
    expect(screen.getByText("Draft")).toBeInTheDocument();
    expect(screen.getByText("Push to Garmin")).toBeInTheDocument();
  });

  it("Push to Garmin calls the push mutation with the workout's id", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(withOne(SCHEDULED));
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Push to Garmin"));

    expect(mockPush).toHaveBeenCalledWith(1);
  });

  it("shows Push to Garmin for a scheduled yoga workout too", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(
      withOne({ ...SCHEDULED, sport: "yoga", scheduled_time: "18:30", estimated_duration_s: 2700 }),
    );
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText("Push to Garmin")).toBeInTheDocument();
    expect(screen.getByText(/18:30/)).toBeInTheDocument();
    expect(screen.getByText(/45 min/)).toBeInTheDocument();
  });

  it("shows a Mark as done button and calls the complete mutation with the workout's id", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(withOne(SCHEDULED));
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.queryByText("Done")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Mark as done"));

    expect(mockComplete).toHaveBeenCalledWith(1);
  });

  it("shows a Done badge and a Mark as not done button once completed_at is set", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(
      withOne({ ...SCHEDULED, completed_at: "2026-09-01T12:00:00" }),
    );
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText("Done")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Mark as not done"));

    expect(mockUncomplete).toHaveBeenCalledWith(1);
  });

  it("shows a Done (via Garmin) badge for a matched activity with no manual complete", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(
      withOne({ ...SCHEDULED, matched_activity_id: "a1" }),
    );
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText("Done (via Garmin)")).toBeInTheDocument();
    // The toggle still tracks the manual marker alone -- nothing to "undo" since completed_at
    // was never set, so it still offers "Mark as done" (to lock it in), not "Mark as not done"
    // (which would misleadingly promise to clear a match it can't actually clear).
    expect(screen.getByText("Mark as done")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Mark as done"));

    expect(mockComplete).toHaveBeenCalledWith(1);
  });

  it("shows the running workout's steps as read-only text detail", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(withOne(SCHEDULED));
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText("Warmup")).toBeInTheDocument();
    expect(screen.getByText("10m")).toBeInTheDocument();
  });

  it("shows a hiit workout's steps as read-only text detail, exercise name included", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(
      withOne({
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
            duration_reps: 10,
            exercise_category: "BENCH_PRESS",
            exercise_name: "",
            weight_kg: 60,
            comment: null,
          },
        ],
      }),
    );
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText(/Bench Press/)).toBeInTheDocument();
    expect(screen.getByText(/10 reps/)).toBeInTheDocument();
    expect(screen.getByText(/60kg/)).toBeInTheDocument();
  });

  it("shows a yoga workout's own notes as read-only text detail", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(
      withOne({
        ...SCHEDULED,
        sport: "yoga",
        source_text: "Bring the good mat",
        steps: [],
      }),
    );
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText("Bring the good mat")).toBeInTheDocument();
  });

  it("shows more than one workout on the same date, each with its own actions", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue({
      data: [SCHEDULED, { ...SCHEDULED, id: 2, name: "Evening HIIT", sport: "hiit" }],
      isLoading: false,
      isError: false,
    });
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText("Tempo run")).toBeInTheDocument();
    expect(screen.getByText("Evening HIIT")).toBeInTheDocument();
    expect(screen.getAllByText("Edit")).toHaveLength(2);
    expect(screen.getByText("Add another workout")).toBeInTheDocument();
  });

  it("Copy writes the full scheduled workout to the clipboard, not just name/source_text", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(
      withOne({
        ...SCHEDULED,
        sport: "yoga",
        name: "Evening yoga",
        source_text: null,
        scheduled_time: "18:30",
        estimated_duration_s: 2700,
        steps: [],
      }),
    );
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

  it("shows a saved workout's general-guidance comment above the load bar and step list", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(
      withOne({ ...SCHEDULED, comment: "Easy effort today, focus on cadence." }),
    );
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.getByText("Easy effort today, focus on cadence.")).toBeInTheDocument();
  });

  it("Copy carries the general-guidance comment through to Paste", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(
      withOne({ ...SCHEDULED, comment: "Legs still sore -- cut it short if needed." }),
    );
    const { unmount } = render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Copy"));
    unmount();

    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-02" />);
    fireEvent.click(screen.getByText("Paste copied workout"));

    expect(
      screen.getByPlaceholderText(/easy effort, focus on cadence/),
    ).toHaveValue("Legs still sore -- cut it short if needed.");
  });

  it("hides Push to Garmin for a sport with no push builder (e.g. legacy 'fitness' data)", () => {
    // "fitness" is no longer offered in the sport dropdown at all, but a workout saved under it
    // before that removal must still degrade gracefully rather than offering a push that would
    // just fail.
    mockUsePlannedWorkoutsForDate.mockReturnValue(withOne({ ...SCHEDULED, sport: "fitness" }));
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    expect(screen.queryByText("Push to Garmin")).not.toBeInTheDocument();
  });

  it("no longer offers fitness as a sport option", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);
    fireEvent.click(screen.getByText("Schedule a workout"));

    expect(screen.queryByRole("option", { name: "Fitness" })).not.toBeInTheDocument();
  });

  describe("hiit/strength_training exercise picker", () => {
    it("switching to strength_training shows the exercise picker instead of the textarea", () => {
      mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
      render(<ScheduleWorkoutForm localDate="2026-09-01" />);
      fireEvent.click(screen.getByText("Schedule a workout"));

      fireEvent.change(screen.getByRole("combobox"), { target: { value: "strength_training" } });

      expect(screen.queryByPlaceholderText(/Warmup 10m/)).not.toBeInTheDocument();
      expect(screen.queryByText("Duration (minutes)")).not.toBeInTheDocument();
      expect(screen.getByText("+ Add exercise")).toBeInTheDocument();
      expect(screen.getByText("+ Add rest")).toBeInTheDocument();
    });

    it("picking a real exercise and saving sends a structured step", () => {
      mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
      render(<ScheduleWorkoutForm localDate="2026-09-01" />);
      fireEvent.click(screen.getByText("Schedule a workout"));
      fireEvent.change(screen.getByRole("combobox"), { target: { value: "strength_training" } });

      fireEvent.click(screen.getByText("+ Add exercise"));
      fireEvent.change(screen.getByPlaceholderText(/Search exercises/), {
        target: { value: "Bench Press" },
      });
      fireEvent.mouseDown(screen.getByRole("button", { name: /^Bench Press/ }));

      fireEvent.click(screen.getByText("Save"));

      expect(mockCreate).toHaveBeenCalledWith(
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
      mockUsePlannedWorkoutsForDate.mockReturnValue(
        withOne({
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
              comment: null,
            },
          ],
        }),
      );
      render(<ScheduleWorkoutForm localDate="2026-09-01" />);
      fireEvent.click(screen.getByText("Edit"));

      expect((screen.getByPlaceholderText(/Search exercises/) as HTMLInputElement).value).toBe(
        "Burpee",
      );
      expect((screen.getByDisplayValue("15") as HTMLInputElement)).toBeInTheDocument();
    });

    it("saving an edit calls the update mutation with the workout's id", () => {
      mockUsePlannedWorkoutsForDate.mockReturnValue(withOne(SCHEDULED));
      render(<ScheduleWorkoutForm localDate="2026-09-01" />);
      fireEvent.click(screen.getByText("Edit"));

      fireEvent.change(screen.getByPlaceholderText(/Warmup 10m/), {
        target: { value: "Warmup 20m" },
      });
      fireEvent.click(screen.getByText("Save"));

      expect(mockUpdate).toHaveBeenCalledWith(
        expect.objectContaining({ workoutId: 1, source_text: "Warmup 20m" }),
        expect.anything(),
      );
    });

    it("building a set of several exercises with rest, repeated, sends one repeat marker after them", () => {
      mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
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

      expect(mockCreate).toHaveBeenCalledWith(
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
      mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
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

      expect(mockCreate).toHaveBeenCalledWith(
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
      mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
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

  it("Delete calls the delete mutation with the workout's id", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(withOne(SCHEDULED));
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Delete"));

    expect(mockDelete).toHaveBeenCalledWith(1);
  });

  it("a copied workout in the clipboard offers a Paste action that pre-fills the form", () => {
    localStorage.setItem(
      "perseverer_workout_clipboard",
      JSON.stringify({ sport: "running", name: "Copied run", source_text: "Warmup 5m" }),
    );
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
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
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Paste copied workout"));

    expect((screen.getByPlaceholderText("e.g. Evening yoga") as HTMLInputElement).value).toBe(
      "Copied yoga",
    );
    expect((screen.getByLabelText("Duration (minutes)") as HTMLInputElement).value).toBe("60");
    expect((screen.getByLabelText("Hour") as HTMLInputElement).value).toBe("7");
    expect((screen.getByLabelText("Minute") as HTMLInputElement).value).toBe("00");
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
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
    render(<ScheduleWorkoutForm localDate="2026-09-01" />);

    fireEvent.click(screen.getByText("Paste copied workout"));

    expect((screen.getByPlaceholderText(/Search exercises/) as HTMLInputElement).value).toBe(
      "Bench Press",
    );
    expect(screen.getByDisplayValue("8")).toBeInTheDocument();
    expect(screen.getByDisplayValue("55")).toBeInTheDocument();
  });

  it("Repeat this schedule reveals the recurrence controls, and creating one calls the mutation", () => {
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
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
    mockUsePlannedWorkoutsForDate.mockReturnValue(NONE);
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
