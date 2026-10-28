import { describe, expect, it } from "vitest";

import { sportUsesShoes } from "./shoeSports";

describe("sportUsesShoes", () => {
  it("is true for the sports done in shoes", () => {
    for (const sport of ["running", "walking", "hiking", "racket"]) {
      expect(sportUsesShoes(sport)).toBe(true);
    }
    expect(sportUsesShoes("training", "strength_training")).toBe(true);
    expect(sportUsesShoes("fitness_equipment", "elliptical")).toBe(true);
  });

  it("is false for rides and other sports without shoes", () => {
    for (const sport of ["cycling", "indoor_cycling", "rowing", "alpine_skiing", "swimming"]) {
      expect(sportUsesShoes(sport)).toBe(false);
    }
    expect(sportUsesShoes("rock_climbing", "bouldering")).toBe(false);
  });

  it("uses the sub-sport for yoga, pilates and breathwork", () => {
    expect(sportUsesShoes("training", "yoga")).toBe(false);
    expect(sportUsesShoes("fitness_equipment", "pilates")).toBe(false);
    expect(sportUsesShoes("training", "breathing")).toBe(false);
  });
});
