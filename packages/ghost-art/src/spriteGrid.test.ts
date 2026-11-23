import { describe, it, expect } from "vitest";
import {
  resolveGrid,
  hexToRgba,
  expandGrid,
  getPixel,
  type SpriteGrid,
} from "./spriteGrid";

describe("hexToRgba", () => {
  it("parses #rrggbb as opaque", () => {
    expect(hexToRgba("#ff8040")).toEqual([255, 128, 64, 255]);
  });
  it("parses #rrggbbaa alpha", () => {
    expect(hexToRgba("#00000080")).toEqual([0, 0, 0, 128]);
  });
  it("parses shorthand #rgb", () => {
    expect(hexToRgba("#f0a")).toEqual([255, 0, 170, 255]);
  });
  it("throws on a malformed hex", () => {
    expect(() => hexToRgba("nope")).toThrow();
  });
});

describe("resolveGrid", () => {
  const grid: SpriteGrid = {
    palette: { b: "#ffffff", e: "#101018" },
    pixels: [
      ["", "b", ""],
      ["b", "e", "b"],
    ],
  };

  it("resolves palette keys to an RGBA raster", () => {
    const r = resolveGrid(grid);
    expect(r.width).toBe(3);
    expect(r.height).toBe(2);
    // center of row 1 is the eye colour
    expect(getPixel(r, 1, 1)).toEqual([16, 16, 24, 255]);
    // (1,0) is body white
    expect(getPixel(r, 1, 0)).toEqual([255, 255, 255, 255]);
  });

  it("treats the empty-string key as a transparent pixel (no silent black)", () => {
    const r = resolveGrid(grid);
    expect(getPixel(r, 0, 0)).toEqual([0, 0, 0, 0]);
  });

  it("throws on an unknown palette key (no silent black)", () => {
    const bad: SpriteGrid = { palette: { b: "#fff" }, pixels: [["b", "z"]] };
    expect(() => resolveGrid(bad)).toThrow(/unknown palette key/i);
  });

  it("right-pads ragged rows with transparent pixels", () => {
    const ragged: SpriteGrid = {
      palette: { b: "#fff" },
      pixels: [["b", "b"], ["b"]],
    };
    const r = resolveGrid(ragged);
    expect(r.width).toBe(2);
    expect(getPixel(r, 1, 1)).toEqual([0, 0, 0, 0]);
  });
});

describe("expandGrid", () => {
  it("expands a compact char-row authoring form into a SpriteGrid string[][]", () => {
    const g = expandGrid({
      legend: { b: "#ffffff", e: "#101018" },
      transparent: " ",
      rows: [" b ", "beb"],
    });
    expect(g.pixels).toEqual([
      ["", "b", ""],
      ["b", "e", "b"],
    ]);
    // and it round-trips through resolveGrid
    const r = resolveGrid(g);
    expect(getPixel(r, 1, 1)).toEqual([16, 16, 24, 255]);
    expect(getPixel(r, 0, 0)).toEqual([0, 0, 0, 0]);
  });
});
