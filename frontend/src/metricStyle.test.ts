import { describe, expect, it } from "vitest";

import { healthMetricStyle, sportStyle } from "./metricStyle";

// The 14 sports actually present across the whole archive, checked against the real database
// rather than guessed from Garmin's enum. If ingestion ever surfaces a 15th, this test won't
// fail -- the fallback covers it -- but the family test below documents the intent.
const REAL_SPORTS = [
  "running",
  "walking",
  "yoga",
  "strength_training",
  "hiking",
  "fitness_equipment",
  "rock_climbing",
  "cycling",
  "hiit",
  "alpine_skiing",
  "rowing",
  "snowshoeing",
  "breathing",
  "racket",
];

describe("sportStyle", () => {
  it("maps every sport in the real archive to a non-neutral family", () => {
    for (const sport of REAL_SPORTS) {
      expect(sportStyle(sport).tone, `${sport} should belong to a family`).not.toBe("neutral");
    }
  });

  it("collapses fourteen sports onto at most five hues, which is the whole point", () => {
    const tones = new Set(REAL_SPORTS.map((s) => sportStyle(s).tone));
    expect(tones.size).toBeLessThanOrEqual(5);
  });

  it("groups by what the activity is, not by name", () => {
    // Trail/terrain: on foot, outdoors, elevation is the interesting axis.
    expect(sportStyle("hiking").tone).toBe(sportStyle("snowshoeing").tone);
    // Strength: the gym family, whatever Garmin happens to call the machine.
    expect(sportStyle("strength_training").tone).toBe(sportStyle("fitness_equipment").tone);
    // ...and a road run is deliberately NOT the same family as a hike.
    expect(sportStyle("running").tone).not.toBe(sportStyle("hiking").tone);
  });

  it("falls back to neutral for a sport nobody has done yet, rather than an arbitrary colour", () => {
    expect(sportStyle("underwater_basket_weaving")).toEqual({
      tone: "neutral",
      icon: "calendar",
    });
  });
});

describe("healthMetricStyle", () => {
  it("gives both heart-rate metrics the same tone", () => {
    expect(healthMetricStyle("resting_heart_rate").tone).toBe("hr");
    expect(healthMetricStyle("max_heart_rate").tone).toBe("hr");
  });

  it("falls back rather than throwing on an unmapped logical metric", () => {
    expect(healthMetricStyle("something_new").tone).toBe("neutral");
  });
});
