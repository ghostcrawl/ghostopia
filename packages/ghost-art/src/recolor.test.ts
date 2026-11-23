import { describe, it, expect } from "vitest";
import { resolveGrid, getPixel, type SpriteGrid } from "./spriteGrid";
import { recolor, applyStatusTint } from "./recolor";
import {
  sectionPaletteFromRamps,
  DEFAULT_STATUS_TINTS,
  luma,
} from "./palette";
import { outline } from "./outline";

const ghost: SpriteGrid = {
  palette: { b: "#e8ecff", s: "#b0b8e0", e: "#101018" },
  pixels: [
    ["", "b", ""],
    ["b", "e", "b"],
    ["s", "b", "s"],
  ],
};

describe("recolor", () => {
  it("swaps the base ramp for a section ramp (same grid, new look)", () => {
    const base = { b: "#e8ecff", s: "#b0b8e0" };
    const section = sectionPaletteFromRamps("research", base, {
      b: "#88ccff",
      s: "#3a6ea5",
    });
    const raster = resolveGrid(ghost);
    const out = recolor(raster, section);
    // body pixel recoloured to the research ramp
    expect(getPixel(out, 1, 0)).toEqual([136, 204, 255, 255]);
    // shade pixel recoloured too
    expect(getPixel(out, 0, 2)).toEqual([58, 110, 165, 255]);
    // unmapped colour (the eye) is untouched
    expect(getPixel(out, 1, 1)).toEqual([16, 16, 24, 255]);
    // original raster is not mutated
    expect(getPixel(raster, 1, 0)).toEqual([232, 236, 255, 255]);
  });

  it("leaves transparent pixels transparent", () => {
    const section = sectionPaletteFromRamps("x", { b: "#e8ecff" }, { b: "#ff0000" });
    const out = recolor(resolveGrid(ghost), section);
    expect(getPixel(out, 0, 0)).toEqual([0, 0, 0, 0]);
  });
});

describe("applyStatusTint", () => {
  const raster = resolveGrid(ghost);

  it("darkens/desaturates for error", () => {
    const out = applyStatusTint(raster, "error");
    const before = getPixel(raster, 1, 0);
    const after = getPixel(out, 1, 0);
    // darker overall
    expect(luma(after[0], after[1], after[2])).toBeLessThan(
      luma(before[0], before[1], before[2]),
    );
  });

  it("brightens for success", () => {
    // use a mid-grey so brightness has room to move up
    const grey: SpriteGrid = { palette: { g: "#808080" }, pixels: [["g"]] };
    const r = resolveGrid(grey);
    const out = applyStatusTint(r, "success");
    const after = getPixel(out, 0, 0);
    expect(luma(after[0], after[1], after[2])).toBeGreaterThan(128);
  });

  it("is identity for an unknown / neutral status", () => {
    const out = applyStatusTint(raster, "idle");
    expect(getPixel(out, 1, 0)).toEqual(getPixel(raster, 1, 0));
  });

  it("exposes a default tint table for error and success", () => {
    expect(DEFAULT_STATUS_TINTS.error).toBeDefined();
    expect(DEFAULT_STATUS_TINTS.success).toBeDefined();
  });
});

describe("outline", () => {
  it("marks 1px cardinal-neighbour edges and leaves interiors unchanged", () => {
    const raster = resolveGrid(ghost);
    const out = outline(raster, [10, 10, 16, 255]);
    // (0,0) was transparent and is cardinally adjacent to body at (1,0)/(0,1) -> outlined
    expect(getPixel(out, 0, 0)).toEqual([10, 10, 16, 255]);
    // an interior opaque pixel keeps its colour
    expect(getPixel(out, 1, 1)).toEqual([16, 16, 24, 255]);
  });

  it("does not touch a transparent pixel with no opaque neighbour", () => {
    const g: SpriteGrid = {
      palette: { b: "#fff" },
      pixels: [
        ["b", "", ""],
        ["", "", ""],
      ],
    };
    const out = outline(resolveGrid(g), [0, 0, 0, 255]);
    // (2,1) has no opaque cardinal neighbour -> stays transparent
    expect(getPixel(out, 2, 1)).toEqual([0, 0, 0, 0]);
  });
});
