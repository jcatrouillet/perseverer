import { describe, expect, it } from "vitest";

import { rasterTileUrlTemplate, TERRAIN_TILE_URL } from "./mapBasemap";

describe("rasterTileUrlTemplate", () => {
  it("uses OpenTopoMap for the terrain background", () => {
    expect(rasterTileUrlTemplate("terrain")).toBe(TERRAIN_TILE_URL);
    expect(TERRAIN_TILE_URL).toContain("opentopomap.org");
  });

  it("uses CARTO's light tiles otherwise", () => {
    expect(rasterTileUrlTemplate("light")).toContain("basemaps.cartocdn.com/light_all");
  });
});
