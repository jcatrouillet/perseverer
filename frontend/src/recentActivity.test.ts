import { describe, expect, it } from "vitest";

import type { ActivitySummary } from "./api/types";
import { finishedAtMs, lastFinishedWithin } from "./recentActivity";

function act(id: string, start: string, durationS: number | null): ActivitySummary {
  return { id, start_time_utc: start, duration_s: durationS, moving_duration_s: null } as never;
}

const NOW = new Date("2026-11-27T12:00:00Z");

describe("lastFinishedWithin", () => {
  it("returns the activity that finished last within the window", () => {
    const early = act("early", "2026-11-27T05:00:00Z", 3600); // ended 06:00
    const late = act("late", "2026-11-27T09:00:00Z", 3600); // ended 10:00
    expect(lastFinishedWithin([early, late], NOW)?.id).toBe("late");
  });

  it("ignores an activity that finished more than 8 hours ago", () => {
    const old = act("old", "2026-11-27T01:00:00Z", 3600); // ended 02:00, 10 h ago
    expect(lastFinishedWithin([old], NOW)).toBeNull();
  });

  it("counts from the end, not the start", () => {
    // Started 9 h ago but ended 6 h ago.
    const longRun = act("long", "2026-11-27T03:00:00Z", 3 * 3600);
    expect(lastFinishedWithin([longRun], NOW)?.id).toBe("long");
  });

  it("ignores an activity that has not ended yet", () => {
    expect(lastFinishedWithin([act("future", "2026-11-27T11:30:00Z", 3600)], NOW)).toBeNull();
  });

  it("reads an offset-less start as UTC and tolerates a missing duration", () => {
    expect(finishedAtMs(act("a", "2026-11-27T10:00:00", null))).toBe(
      Date.parse("2026-11-27T10:00:00Z"),
    );
    expect(lastFinishedWithin([act("a", "2026-11-27T10:00:00", null)], NOW)?.id).toBe("a");
  });

  it("returns null for no activities", () => {
    expect(lastFinishedWithin([], NOW)).toBeNull();
  });
});
