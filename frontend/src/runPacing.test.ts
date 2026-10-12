import { describe, expect, it } from "vitest";

import { pacingText } from "./runPacing";

const detail = {
  split: "negative",
  first_half_s_per_km: 436,
  second_half_s_per_km: 405,
  start_s_per_km: 391,
  finish_s_per_km: 401,
  negative_km_splits: 10,
  second_half_km_count: 16,
};

describe("pacingText", () => {
  it("describes a negative split in km", () => {
    const text = pacingText(detail, "server title", "metric");
    expect(text.title).toBe("Negative split: second half 0:31 /km faster than the first");
    expect(text.paces).toBe(
      "First half 7:16 /km · second half 6:45 /km · first 2 km 6:31 /km · last 2 km 6:41 /km · " +
        "10 of 16 second-half km faster than the first half",
    );
  });

  it("uses miles for an imperial athlete", () => {
    const text = pacingText(detail, "server title", "imperial");
    expect(text.title).toBe("Negative split: second half 0:50 /mi faster than the first");
    expect(text.paces).toContain("First half 11:42 /mi");
  });

  it("names positive and even splits", () => {
    expect(pacingText({ ...detail, split: "positive" }, "", "metric").title).toMatch(
      /^Positive split: second half 0:31 \/km slower/,
    );
    expect(pacingText({ ...detail, split: "even" }, "", "metric").title).toBe(
      "Even split: both halves at the same pace",
    );
  });

  it("falls back to the server's title without paces", () => {
    expect(pacingText({}, "Negative split", "metric")).toEqual({
      title: "Negative split",
      paces: null,
    });
  });
});
