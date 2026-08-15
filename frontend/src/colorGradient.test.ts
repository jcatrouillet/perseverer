import { describe, expect, it } from "vitest";

import { hexToRgb, lerpColor } from "./colorGradient";

describe("hexToRgb", () => {
  it("parses a 6-digit hex colour", () => {
    expect(hexToRgb("#4da3ff")).toEqual([77, 163, 255]);
  });

  it("parses a 3-digit shorthand hex colour", () => {
    expect(hexToRgb("#fff")).toEqual([255, 255, 255]);
  });

  it("falls back to grey for an unparseable value", () => {
    expect(hexToRgb("not-a-colour")).toEqual([128, 128, 128]);
  });
});

describe("lerpColor", () => {
  it("returns the start colour at t=0 and end colour at t=1", () => {
    expect(lerpColor([255, 0, 0], [0, 0, 255], 0)).toBe("rgb(255, 0, 0)");
    expect(lerpColor([255, 0, 0], [0, 0, 255], 1)).toBe("rgb(0, 0, 255)");
  });

  it("interpolates at the midpoint", () => {
    expect(lerpColor([0, 0, 0], [200, 100, 50], 0.5)).toBe("rgb(100, 50, 25)");
  });

  it("clamps t outside [0, 1]", () => {
    expect(lerpColor([0, 0, 0], [200, 100, 50], -1)).toBe("rgb(0, 0, 0)");
    expect(lerpColor([0, 0, 0], [200, 100, 50], 2)).toBe("rgb(200, 100, 50)");
  });
});
