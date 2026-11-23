import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { loadManifest, loadPalettes } from "./manifest";
import { loadAnimations } from "./animations";
import { buildAtlas } from "./buildAtlas";
import { expandGrid, type SpriteGrid } from "./spriteGrid";

const assetsDir = resolve(dirname(fileURLToPath(import.meta.url)), "../../../assets");
const readAsset = (p: string) => JSON.parse(readFileSync(resolve(assetsDir, p), "utf8"));

const grids: Record<string, SpriteGrid> = {
  ghost_idle: expandGrid({
    legend: { b: "#e8ecff", e: "#101018" },
    transparent: " ",
    rows: [" bb ", "beeb", " bb "],
  }),
  grave: expandGrid({
    legend: { d: "#5a4b6e" },
    transparent: " ",
    rows: ["dddd", "dddd"],
  }),
};

const manifestJson = {
  cellDefault: { width: 4, height: 3 },
  sprites: {
    ghost: {
      grid: "ghost_idle",
      cellWidth: 4,
      cellHeight: 3,
      regions: {
        idle: { col: 0, row: 0, cols: 1, rows: 1 },
      },
    },
    grave: {
      grid: "grave",
      cellWidth: 4,
      cellHeight: 2,
      regions: { home: { col: 0, row: 0, cols: 1, rows: 1 } },
    },
  },
};

const palettesJson = {
  base: { b: "#e8ecff", s: "#b0b8e0", e: "#101018" },
  sections: {
    research: { b: "#88ccff", s: "#3a6ea5" },
    extraction: { b: "#ffc07a", s: "#a5602a" },
  },
  statusTints: {
    error: { brightness: 0.7, saturation: 0.4 },
    success: { brightness: 1.2, saturation: 1.1 },
  },
};

describe("loadManifest", () => {
  it("returns a typed manifest with region frame tables", () => {
    const m = loadManifest(manifestJson, Object.keys(grids));
    expect(m.sprites.ghost.grid).toBe("ghost_idle");
    expect(m.sprites.ghost.regions.idle).toEqual({
      col: 0,
      row: 0,
      cols: 1,
      rows: 1,
    });
  });

  it("rejects a manifest whose grid ref is missing", () => {
    const broken = {
      sprites: {
        ghost: { grid: "does_not_exist", cellWidth: 4, cellHeight: 3, regions: {} },
      },
    };
    expect(() => loadManifest(broken, Object.keys(grids))).toThrow(
      /missing grid|does_not_exist/i,
    );
  });

  it("rejects a structurally invalid manifest", () => {
    expect(() => loadManifest({ nope: true }, [])).toThrow();
  });
});

describe("loadPalettes", () => {
  it("resolves one base ramp + per-section palettes + per-status tints", () => {
    const p = loadPalettes(palettesJson);
    expect(p.base.b).toBe("#e8ecff");
    expect(p.sections.research.name).toBe("research");
    // section palette maps base hex -> section hex
    expect(p.sections.research.map["#e8ecff"]).toBe("#88ccff");
    expect(p.statusTints.error.brightness).toBeLessThan(1);
    expect(p.statusTints.success.brightness).toBeGreaterThan(1);
  });
});

describe("real assets — animations.json ⟷ sprites.manifest.json contract", () => {
  it("every clip frame in animations.json resolves to a manifest region", () => {
    const manifest = loadManifest(readAsset("sprites.manifest.json"));
    const book = loadAnimations(readAsset("animations.json"), manifest);
    // sanity: the 8-way turn set is present (5 authored + 3 mirrored)
    for (const f of ["s", "se", "e", "ne", "n", "sw", "w", "nw"]) {
      expect(book[`idle.${f}`]).toBeDefined();
      expect(book[`move.${f}`]).toBeDefined();
    }
    // the west set is a mirror of the authored east art
    expect(book["idle.w"].mirror).toBe(true);
    expect(book["idle.e"].mirror).toBe(false);
    // living overlays + ambient world clips exist
    for (const c of ["work", "success", "error", "ov.captcha", "ov.zzz", "world.terminal", "world.candle"]) {
      expect(book[c]).toBeDefined();
    }
  });

  it("the ghost sprite catalogues 5 authored facings x (idle0..3 + move0..3) + expression frames", () => {
    const manifest = loadManifest(readAsset("sprites.manifest.json"));
    const ghost = manifest.sprites.ghost.regions;
    for (const f of ["s", "se", "e", "ne", "n"]) {
      for (let i = 0; i < 4; i++) {
        expect(ghost[`${f}_idle${i}`]).toBeDefined();
        expect(ghost[`${f}_move${i}`]).toBeDefined();
      }
    }
    for (const e of ["work0", "work1", "work2", "success0", "success1", "error0", "error1"]) {
      expect(ghost[e]).toBeDefined();
    }
    // the west set is NOT authored as its own regions (mirror-only)
    expect(ghost["w_idle0"]).toBeUndefined();
  });
});

describe("buildAtlas", () => {
  it("packs manifest grids into a raster atlas + a frame table (no renderer dependency)", () => {
    const m = loadManifest(manifestJson, Object.keys(grids));
    const atlas = buildAtlas(m, grids);
    expect(atlas.width).toBeGreaterThan(0);
    expect(atlas.height).toBeGreaterThan(0);
    expect(atlas.data.length).toBe(atlas.width * atlas.height * 4);
    // one frame per sprite:region
    expect(atlas.frames["ghost:idle"]).toBeDefined();
    expect(atlas.frames["grave:home"]).toBeDefined();
    const f = atlas.frames["ghost:idle"];
    expect(f.w).toBe(4);
    expect(f.h).toBe(3);
  });
});
