import { describe, expect, it } from "vitest";

import { formatClock, formatHHMM, formatTimeOfDay } from "./formatTime";

describe("formatClock", () => {
  it("formats 24h with zero-padded hour and minute", () => {
    expect(formatClock(6, 32, "24h")).toBe("06:32");
    expect(formatClock(18, 5, "24h")).toBe("18:05");
  });

  it("formats 12h with AM for the morning", () => {
    expect(formatClock(6, 32, "12h")).toBe("6:32 AM");
  });

  it("formats 12h with PM for the afternoon/evening", () => {
    expect(formatClock(18, 5, "12h")).toBe("6:05 PM");
  });

  it("formats midnight as 12 AM, not 0 AM", () => {
    expect(formatClock(0, 0, "12h")).toBe("12:00 AM");
  });

  it("formats noon as 12 PM, not 0 PM", () => {
    expect(formatClock(12, 0, "12h")).toBe("12:00 PM");
  });
});

describe("formatHHMM", () => {
  it("parses a stored 24h HH:MM string and reformats per the given format", () => {
    expect(formatHHMM("06:32", "24h")).toBe("06:32");
    expect(formatHHMM("06:32", "12h")).toBe("6:32 AM");
    expect(formatHHMM("18:05", "12h")).toBe("6:05 PM");
  });
});

describe("formatTimeOfDay", () => {
  it("formats a Date's own local hour/minute", () => {
    const d = new Date();
    d.setHours(14, 45, 0, 0);
    expect(formatTimeOfDay(d, "24h")).toBe("14:45");
    expect(formatTimeOfDay(d, "12h")).toBe("2:45 PM");
  });
});
