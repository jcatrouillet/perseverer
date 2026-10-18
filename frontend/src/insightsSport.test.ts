import { describe, expect, it } from "vitest";

import { tabAvailable, tabLabel } from "./insightsSport";

describe("insights sport tabs", () => {
  it("hides the running race models for cycling and keeps every other tab", () => {
    expect(tabAvailable("vo2max", "cycling")).toBe(true);
    expect(tabAvailable("race-predictions", "cycling")).toBe(false);
    expect(tabAvailable("race-readiness", "cycling")).toBe(false);
    expect(tabAvailable("eddington", "cycling")).toBe(true);
    expect(tabAvailable("vo2max", "running")).toBe(true);
  });

  it("names the cycling versions by what they measure", () => {
    expect(tabLabel("pace-trends", "cycling")).toBe("Power trends");
    expect(tabLabel("pace-hr-zones", "cycling")).toBe("HR zones");
    expect(tabLabel("pace-trends", "running")).toBe("Pace trends");
    expect(tabLabel("vo2max", "cycling")).toBe("FTP & VO2max");
    expect(tabLabel("vo2max", "running")).toBe("VO2max");
  });
});
