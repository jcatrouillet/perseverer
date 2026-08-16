import { describe, expect, it } from "vitest";

import type { InsightOut } from "./api/types";
import { CURRENT_WINDOW, groupByKind, visibleInsights } from "./insightsView";

function insight(overrides: Partial<InsightOut> = {}): InsightOut {
  return {
    kind: "effort",
    window: "30d",
    title: "Longest distance (run)",
    detail: {},
    value_num: 10000,
    metric_key: null,
    sport_family: "run",
    activity_id: "a1",
    local_date: "2026-08-10",
    computed_at: "2026-08-14T00:00:00Z",
    ...overrides,
  };
}

describe("visibleInsights", () => {
  it("includes only the selected window for windowed kinds", () => {
    const insights = [
      insight({ window: "30d" }),
      insight({ window: "90d" }),
    ];
    expect(visibleInsights(insights, "30d")).toHaveLength(1);
    expect(visibleInsights(insights, "30d")[0].window).toBe("30d");
  });

  it("always includes current-window insights regardless of the selected window", () => {
    const insights = [
      insight({ window: CURRENT_WINDOW, kind: "streak" }),
      insight({ window: "90d" }),
    ];
    const visible = visibleInsights(insights, "30d");
    expect(visible).toHaveLength(1);
    expect(visible[0].window).toBe(CURRENT_WINDOW);
  });
});

describe("groupByKind", () => {
  it("groups insights by their kind, preserving order within a group", () => {
    const insights = [
      insight({ kind: "effort", title: "first" }),
      insight({ kind: "streak", title: "second" }),
      insight({ kind: "effort", title: "third" }),
    ];
    const groups = groupByKind(insights);
    expect(groups.get("effort")?.map((i) => i.title)).toEqual(["first", "third"]);
    expect(groups.get("streak")?.map((i) => i.title)).toEqual(["second"]);
  });

  it("returns an empty map for an empty list", () => {
    expect(groupByKind([]).size).toBe(0);
  });
});
