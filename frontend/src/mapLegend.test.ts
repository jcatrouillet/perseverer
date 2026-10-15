import { describe, expect, it } from "vitest";

import { mapLegendEntries } from "./mapLegend";

describe("mapLegendEntries", () => {
  it("has one entry per colour on the map, naming its sports, most frequent first", () => {
    const points = [
      ...Array(3).fill({ sport: "running" }),
      { sport: "cycling" },
      ...Array(2).fill({ sport: "hiking" }),
      { sport: "alpine_skiing" },
      { sport: "something_new" },
    ];
    expect(mapLegendEntries(points)).toEqual([
      { tone: "pace", color: "var(--color-pace)", label: "Running, cycling", count: 4 },
      {
        tone: "elevation",
        color: "var(--color-elevation)",
        label: "Hiking, alpine skiing",
        count: 3,
      },
      { tone: "neutral", color: "var(--color-text-muted)", label: "Something new", count: 1 },
    ]);
  });

  it("is empty with no points", () => {
    expect(mapLegendEntries([])).toEqual([]);
  });
});
