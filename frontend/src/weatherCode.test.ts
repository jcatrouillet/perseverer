import { describe, expect, it } from "vitest";

import { weatherCodeInfo } from "./weatherCode";

describe("weatherCodeInfo", () => {
  it("maps clear-sky codes to the sun icon", () => {
    expect(weatherCodeInfo(0).icon).toBe("sun");
    expect(weatherCodeInfo(1).icon).toBe("sun");
  });

  it("maps cloud codes to the cloud icon", () => {
    expect(weatherCodeInfo(2).icon).toBe("cloud");
    expect(weatherCodeInfo(3).icon).toBe("cloud");
  });

  it("maps every rain/drizzle/shower code to the rain icon", () => {
    for (const code of [51, 55, 61, 65, 80, 82]) {
      expect(weatherCodeInfo(code).icon).toBe("rain");
    }
  });

  it("maps every snow code to the snow icon", () => {
    for (const code of [71, 75, 77, 85, 86]) {
      expect(weatherCodeInfo(code).icon).toBe("snow");
    }
  });

  it("maps thunderstorm codes to the storm icon", () => {
    expect(weatherCodeInfo(95).icon).toBe("storm");
    expect(weatherCodeInfo(99).icon).toBe("storm");
  });

  it("maps fog codes to the fog icon", () => {
    expect(weatherCodeInfo(45).icon).toBe("fog");
    expect(weatherCodeInfo(48).icon).toBe("fog");
  });

  it("falls back to a generic cloud icon for an unrecognized code, never throwing", () => {
    const info = weatherCodeInfo(9999);
    expect(info.icon).toBe("cloud");
    expect(info.label).toBeTruthy();
  });
});
