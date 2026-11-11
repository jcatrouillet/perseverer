import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Logbook, PhotoOut } from "../api/types";
import { LogbookCard } from "./LogbookCard";

const save = vi.fn();
let photos: PhotoOut[] = [];
let logbook: Logbook = { exercises: [] };

const idle = { isPending: false, isError: false, error: null, mutate: vi.fn() };

vi.mock("../api/queries", () => ({
  useLogbookStatus: () => ({ data: { can_read_handwriting: true } }),
  useActivityPhotos: () => ({ data: photos }),
  useActivityLogbook: () => ({ data: logbook }),
  useActivityExerciseSets: () => ({
    data: [
      {
        set_index: 0,
        set_type: "ACTIVE",
        reps: 10,
        exercise_category: "SQUAT",
        exercise_name: "AIR_SQUAT",
      },
      { set_index: 1, set_type: "ACTIVE", reps: 12, exercise_category: "CURL", exercise_name: "X" },
    ],
  }),
  useUploadActivityPhoto: () => idle,
  useDeleteActivityPhoto: () => idle,
  useReadActivityPhoto: () => idle,
  usePhotoUrl: () => ({ data: "blob:photo" }),
  useSaveActivityLogbook: () => ({ ...idle, mutate: save }),
}));
vi.mock("../exerciseLibrary", () => ({
  loadExerciseLibrary: () =>
    Promise.resolve([
      {
        name: "Air Squat",
        category: "SQUAT",
        categoryLabel: "Squat",
        exercise: "AIR_SQUAT",
        primary_muscles: ["Quads"],
        secondary_muscles: [],
      },
    ]),
}));

const photo: PhotoOut = {
  id: 1,
  content_type: "image/png",
  byte_size: 10,
  created_at: "2026-11-01T00:00:00Z",
  decode_status: "done",
  decode_error: null,
  decoded_at: null,
  decoded: {
    legible: true,
    transcript: "",
    exercises: [
      { name: "Curl", note: null, sets: [{ reps: 12, weight: 10, unit: "kg", duration_s: null }] },
      {
        name: "Air squat",
        note: null,
        sets: [{ reps: 10, weight: null, unit: null, duration_s: null }],
      },
    ],
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  photos = [photo];
  logbook = { exercises: [] };
});

describe("LogbookCard", () => {
  it("loads a reading into the editor, matches the library and saves in the order the athlete sets", async () => {
    render(<LogbookCard activityId="a1" />);
    fireEvent.click(screen.getByRole("button", { name: "Use this reading" }));
    await waitFor(() => expect(screen.getByText("Air Squat")).toBeInTheDocument());

    // Curl was written first, the watch did the squat first: order like the watch.
    fireEvent.click(screen.getByRole("button", { name: "Order like the watch" }));
    const names = screen
      .getAllByLabelText(/Exercise \d name/)
      .map((i) => (i as HTMLInputElement).value);
    expect(names).toEqual(["Air squat", "Curl"]);

    fireEvent.click(screen.getByRole("button", { name: "Save the sets" }));
    const body = save.mock.calls[0]![0] as Logbook;
    expect(body.exercises.map((e) => [e.label, e.exercise_name])).toEqual([
      ["Air squat", "AIR_SQUAT"],
      ["Curl", null],
    ]);
  });

  it("moves an exercise by hand", async () => {
    logbook = {
      exercises: [
        {
          label: "A",
          exercise_category: null,
          exercise_name: null,
          note: null,
          photo_id: null,
          sets: [{ reps: 1, weight_kg: null, duration_s: null }],
        },
        {
          label: "B",
          exercise_category: null,
          exercise_name: null,
          note: null,
          photo_id: null,
          sets: [{ reps: 1, weight_kg: null, duration_s: null }],
        },
      ],
    };
    render(<LogbookCard activityId="a1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Move A down" }));
    const names = screen
      .getAllByLabelText(/Exercise \d name/)
      .map((i) => (i as HTMLInputElement).value);
    expect(names).toEqual(["B", "A"]);
  });
});
