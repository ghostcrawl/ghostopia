import { describe, expect, it } from "vitest";

import { areasForSection, loadMapData, sectionForArea, type Area } from "./mapData.js";

const AREAS: Area[] = [
  { id: "crypt", section: "home", x: 0, y: 0, w: 24, h: 3 },
  { id: "ghost-graves", section: "home", x: 0, y: 3, w: 6, h: 9 },
  { id: "central-graveyard", section: "research", x: 6, y: 3, w: 12, h: 9 },
  { id: "data-graveyard", section: "verify", x: 12, y: 12, w: 12, h: 6 },
];

describe("painted section plots — area↔section mapping ( P17)", () => {
  it("maps a painted area to its section", () => {
    expect(sectionForArea(AREAS, "central-graveyard")).toBe("research");
    expect(sectionForArea(AREAS, "data-graveyard")).toBe("verify");
  });

  it("returns null for an unknown area id", () => {
    expect(sectionForArea(AREAS, "nope")).toBeNull();
  });

  it("finds every plot a section spans (a section may own multiple plots)", () => {
    const home = areasForSection(AREAS, "home");
    expect(home.map((a) => a.id).sort()).toEqual(["crypt", "ghost-graves"]);
    expect(areasForSection(AREAS, "research")).toHaveLength(1);
    expect(areasForSection(AREAS, "missing")).toEqual([]);
  });

  it("loadMapData parses an explicit areas layer", () => {
    const map = loadMapData({
      name: "t",
      width: 4,
      height: 4,
      tile_size: 16,
      walkable: [
        [1, 1, 1, 1],
        [1, 1, 1, 1],
        [1, 1, 1, 1],
        [1, 1, 1, 1],
      ],
      regions: { a: { x: 0, y: 0, w: 2, h: 2 } },
      areas: [{ id: "a", section: "research", x: 0, y: 0, w: 2, h: 2 }],
    });
    expect(map.areas).toHaveLength(1);
    expect(sectionForArea(map.areas, "a")).toBe("research");
  });

  it("falls back to one plot per region when no areas layer is present", () => {
    const map = loadMapData({
      name: "t",
      width: 2,
      height: 2,
      tile_size: 16,
      walkable: [
        [1, 1],
        [1, 1],
      ],
      regions: { research: { x: 0, y: 0, w: 2, h: 2 } },
    });
    // each region becomes its own plot mapping to a same-named section.
    expect(map.areas).toEqual([{ id: "research", section: "research", x: 0, y: 0, w: 2, h: 2 }]);
  });
});

describe("placed-props layer ( P19)", () => {
  const base = {
    name: "t",
    width: 4,
    height: 4,
    tile_size: 16,
    walkable: [
      [1, 1, 1, 1],
      [1, 1, 1, 1],
      [1, 1, 1, 1],
      [1, 1, 1, 1],
    ],
    regions: { a: { x: 0, y: 0, w: 4, h: 4 } },
  };

  it("parses placed props into typed {catalogId, tile, orientation, state}", () => {
    const map = loadMapData({
      ...base,
      placed_props: [
        { catalog_id: "lantern", tile: [1, 2], orientation: "s", state: "on" },
        { catalog_id: "fence", tile: [0, 0] },
      ],
    });
    expect(map.placedProps).toHaveLength(2);
    expect(map.placedProps[0]).toEqual({
      catalogId: "lantern",
      tile: { x: 1, y: 2 },
      orientation: "s",
      state: "on",
    });
    // orientation defaults to "s", state to null when omitted.
    expect(map.placedProps[1]).toEqual({
      catalogId: "fence",
      tile: { x: 0, y: 0 },
      orientation: "s",
      state: null,
    });
  });

  it("defaults to an empty placed-props layer when absent", () => {
    expect(loadMapData(base).placedProps).toEqual([]);
  });

  it("throws on a malformed placed prop (missing catalog_id or bad tile)", () => {
    expect(() => loadMapData({ ...base, placed_props: [{ tile: [0, 0] }] })).toThrow(/catalog_id/);
    expect(() => loadMapData({ ...base, placed_props: [{ catalog_id: "x", tile: [0] }] })).toThrow(
      /tile/,
    );
  });
});
