import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ExerciseLibraryEntry } from "../exerciseLibrary";

const mockLoadExerciseLibrary = vi.fn();

vi.mock("../exerciseLibrary", async () => {
  const actual = await vi.importActual<typeof import("../exerciseLibrary")>("../exerciseLibrary");
  return {
    ...actual,
    loadExerciseLibrary: () => mockLoadExerciseLibrary(),
  };
});

import { ExerciseLibraryPage } from "./ExerciseLibraryPage";

function entry(overrides: Partial<ExerciseLibraryEntry>): ExerciseLibraryEntry {
  return {
    name: "Barbell Bench Press",
    category: "BENCH_PRESS",
    categoryLabel: "Bench Press",
    exercise: "BARBELL_BENCH_PRESS",
    garmin_url: "https://connect.garmin.com/modern/exercises/BENCH_PRESS/BARBELL_BENCH_PRESS",
    primary_muscles: ["Chest"],
    secondary_muscles: ["Triceps"],
    tier: 1,
    image_url: "https://connect.garmin.com/images/exercises/images/BENCH_PRESS/hero.jpg",
    image_source: "garmin",
    description: "A classic upper-body pressing exercise.",
    difficulty: "Beginner",
    ...overrides,
  };
}

const FIXTURES: ExerciseLibraryEntry[] = [
  entry({}),
  entry({
    name: "Air Squat",
    category: "SQUAT",
    categoryLabel: "Squat",
    exercise: "AIR_SQUAT",
    garmin_url: "https://connect.garmin.com/modern/exercises/SQUAT/AIR_SQUAT",
    primary_muscles: ["Quads", "Glutes"],
    secondary_muscles: [],
    tier: 2,
    image_url: "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/Squat/0.jpg",
    image_source: "free-exercise-db",
    description: "Stand with feet shoulder-width apart and squat down.",
    difficulty: "Intermediate",
    matched_exercise_name: "Squat",
  }),
  entry({
    name: "Banded Ab Twist",
    category: "BANDED_EXERCISES",
    categoryLabel: "Banded Exercises",
    exercise: "AB_TWIST",
    garmin_url: "https://connect.garmin.com/modern/exercises/BANDED_EXERCISES/AB_TWIST",
    primary_muscles: ["Abs", "Obliques"],
    secondary_muscles: [],
    tier: 3,
    image_url: null,
    image_source: null,
    description: "Primarily targets the abs, obliques.",
    difficulty: null,
  }),
];

describe("ExerciseLibraryPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLoadExerciseLibrary.mockResolvedValue(FIXTURES);
  });

  it("shows a loading state before the library resolves", () => {
    mockLoadExerciseLibrary.mockReturnValue(new Promise(() => {}));
    render(<ExerciseLibraryPage />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders categories collapsed by default, with a per-category count", async () => {
    render(<ExerciseLibraryPage />);
    await waitFor(() => expect(screen.getByText("Bench Press")).toBeInTheDocument());

    // The exercise name is inside a collapsed <details> -- present in the DOM but not visible.
    const details = screen.getByText("Bench Press").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(details?.querySelector(".exercise-library__count")).toHaveTextContent("1");
  });

  it("shows a real photo for tier 1/2 exercises and a placeholder for tier 3", async () => {
    const { container } = render(<ExerciseLibraryPage />);
    await waitFor(() => expect(screen.getByText("Barbell Bench Press")).toBeInTheDocument());

    // These thumbnails are decorative (alt="") so they don't carry an accessible "img" role --
    // query the DOM directly rather than by role.
    const images = Array.from(container.querySelectorAll("img"));
    expect(images.some((img) => img.getAttribute("src")?.includes("BENCH_PRESS"))).toBe(true);
    expect(screen.getByText("No photo")).toBeInTheDocument();
  });

  it("links every exercise to its own Garmin Connect page", async () => {
    render(<ExerciseLibraryPage />);
    await waitFor(() => expect(screen.getAllByText("View on Garmin Connect")).toHaveLength(3));

    // Categories render alphabetically by label: Banded Exercises, Bench Press, Squat.
    const links = screen.getAllByText("View on Garmin Connect") as HTMLAnchorElement[];
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "https://connect.garmin.com/modern/exercises/BANDED_EXERCISES/AB_TWIST",
      "https://connect.garmin.com/modern/exercises/BENCH_PRESS/BARBELL_BENCH_PRESS",
      "https://connect.garmin.com/modern/exercises/SQUAT/AIR_SQUAT",
    ]);
  });

  it("notes when a tier-2 photo belongs to a closely related exercise, not the exact variant", async () => {
    render(<ExerciseLibraryPage />);
    await waitFor(() => expect(screen.getByText("Air Squat")).toBeInTheDocument());

    expect(screen.getByText(/closely related exercise: Squat/)).toBeInTheDocument();
  });

  it("searching filters exercises and auto-expands matching categories", async () => {
    render(<ExerciseLibraryPage />);
    await waitFor(() => expect(screen.getByText("Bench Press")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/Search an exercise/), {
      target: { value: "squat" },
    });

    expect(screen.queryByText("Bench Press")).not.toBeInTheDocument();
    expect(screen.getByText("Air Squat").closest("details")).toHaveAttribute("open");
    expect(screen.getByText("1 match")).toBeInTheDocument();
  });

  it("shows a no-matches message for a search with no results", async () => {
    render(<ExerciseLibraryPage />);
    await waitFor(() => expect(screen.getByText("Bench Press")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/Search an exercise/), {
      target: { value: "nonexistent exercise xyz" },
    });

    expect(screen.getByText(/No exercise matches/)).toBeInTheDocument();
  });
});
