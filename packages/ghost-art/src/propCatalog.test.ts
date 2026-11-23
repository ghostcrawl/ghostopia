// ghostopia ghost-art — prop catalog loader tests (Task 1, TDD RED-first).
//
// The prop catalog is the DATA MODEL the Graveyard Builder edits: each prop
// is fully described by its catalog def + a placed {tile, orientation, state}. These
// tests pin the load/validate contract (missing grid/region/clip ref throws loudly),
// the orientation resolve (incl. mirror), the on/off state resolve, and footprint parse.

import { describe, expect, it } from "vitest";

import type { SpriteManifest } from "./manifest.js";
import type { AnimationBook } from "./animations.js";
import { loadPropCatalog, resolvePropRegion } from "./propCatalog.js";

/** A minimal manifest with the sprites/regions the catalog fixtures reference. */
const MANIFEST: SpriteManifest = {
  sprites: {
    lantern: {
      grid: "lantern",
      cellWidth: 16,
      cellHeight: 16,
      regions: {
        off: { col: 0, row: 0, cols: 1, rows: 1 },
        on0: { col: 1, row: 0, cols: 1, rows: 1 },
        on1: { col: 2, row: 0, cols: 1, rows: 1 },
      },
    },
    bench: {
      grid: "bench",
      cellWidth: 16,
      cellHeight: 16,
      regions: { side: { col: 0, row: 0, cols: 1, rows: 1 } },
    },
    tree: {
      grid: "tree",
      cellWidth: 16,
      cellHeight: 24,
      regions: { base: { col: 0, row: 0, cols: 1, rows: 1 } },
    },
  },
};

const BOOK: AnimationBook = {
  "prop.lantern": {
    frames: [
      { sprite: "lantern", region: "on0", ms: 240 },
      { sprite: "lantern", region: "on1", ms: 200 },
    ],
    loop: true,
    mirror: false,
  },
};

/** A well-formed catalog covering: state pair (lantern), 2-way orientation via mirror (bench),
 * a multi-tile footprint, an anim clip, and a plain single-orientation prop (tree). */
function goodCatalog(): unknown {
  return {
    props: {
      lantern: {
        category: "light",
        sprite: "lantern",
        footprint: { w: 1, h: 1 },
        placement: "ground",
        orientations: { s: { region: "off" } },
        defaultOrientation: "s",
        states: { off: "off", on: "on0" },
        defaultState: "off",
        anim: "prop.lantern",
      },
      bench: {
        category: "furniture",
        sprite: "bench",
        footprint: { w: 2, h: 1 },
        placement: "ground",
        orientations: { e: { region: "side" }, w: { mirror: "e" } },
        defaultOrientation: "e",
      },
      tree: {
        category: "flora",
        sprite: "tree",
        footprint: { w: 1, h: 1 },
        placement: "ground",
        orientations: { s: { region: "base" } },
        defaultOrientation: "s",
      },
    },
  };
}

describe("loadPropCatalog", () => {
  it("loads a well-formed catalog and exposes props + footprint + resolved cell size", () => {
    const cat = loadPropCatalog(goodCatalog(), MANIFEST, BOOK);
    expect(Object.keys(cat.props).sort()).toEqual(["bench", "lantern", "tree"]);
    const lantern = cat.props.lantern;
    expect(lantern.category).toBe("light");
    expect(lantern.sprite).toBe("lantern");
    expect(lantern.footprint).toEqual({ w: 1, h: 1 });
    // resolved cell dimensions come from the manifest sprite (renderer needs them)
    expect(lantern.cellWidth).toBe(16);
    expect(lantern.cellHeight).toBe(16);
    expect(cat.props.bench.footprint).toEqual({ w: 2, h: 1 });
    expect(cat.props.tree.cellHeight).toBe(24);
  });

  it("throws when a prop references a sprite absent from the manifest", () => {
    const bad = goodCatalog() as { props: Record<string, { sprite: string }> };
    bad.props.tree.sprite = "nope";
    expect(() => loadPropCatalog(bad, MANIFEST, BOOK)).toThrow(/nope/);
  });

  it("throws when an orientation references a region absent from the sprite", () => {
    const bad = goodCatalog() as {
      props: Record<string, { orientations: Record<string, { region?: string }> }>;
    };
    bad.props.tree.orientations.s.region = "missing";
    expect(() => loadPropCatalog(bad, MANIFEST, BOOK)).toThrow(/missing/);
  });

  it("throws when a state references a region absent from the sprite", () => {
    const bad = goodCatalog() as {
      props: Record<string, { states?: Record<string, string> }>;
    };
    bad.props.lantern.states = { off: "off", on: "ghost" };
    expect(() => loadPropCatalog(bad, MANIFEST, BOOK)).toThrow(/ghost/);
  });

  it("throws when an anim clip is absent from the animation book", () => {
    const bad = goodCatalog() as { props: Record<string, { anim?: string }> };
    bad.props.lantern.anim = "prop.nonexistent";
    expect(() => loadPropCatalog(bad, MANIFEST, BOOK)).toThrow(/prop\.nonexistent/);
  });

  it("skips clip validation when no animation book is supplied", () => {
    // without a book, an anim ref is carried through but not validated (renderer resolves later).
    expect(() => loadPropCatalog(goodCatalog(), MANIFEST)).not.toThrow();
  });

  it("throws when a mirror orientation names an unknown base orientation", () => {
    const bad = goodCatalog() as {
      props: Record<string, { orientations: Record<string, { mirror?: string }> }>;
    };
    bad.props.bench.orientations.w.mirror = "north";
    expect(() => loadPropCatalog(bad, MANIFEST, BOOK)).toThrow(/north/);
  });

  it("throws when defaultOrientation is not one of the declared orientations", () => {
    const bad = goodCatalog() as { props: Record<string, { defaultOrientation: string }> };
    bad.props.tree.defaultOrientation = "q";
    expect(() => loadPropCatalog(bad, MANIFEST, BOOK)).toThrow(/q/);
  });

  it("throws on an unknown placement", () => {
    const bad = goodCatalog() as { props: Record<string, { placement: string }> };
    bad.props.tree.placement = "ceiling";
    expect(() => loadPropCatalog(bad, MANIFEST, BOOK)).toThrow(/ceiling/);
  });
});

describe("resolvePropRegion", () => {
  const cat = loadPropCatalog(goodCatalog(), MANIFEST, BOOK);

  it("resolves a plain orientation to its region with no mirror", () => {
    const r = resolvePropRegion(cat.props.tree, "s");
    expect(r).toEqual({ region: "base", mirror: false });
  });

  it("resolves a mirror orientation to the base region with mirror=true", () => {
    const r = resolvePropRegion(cat.props.bench, "w");
    expect(r).toEqual({ region: "side", mirror: true });
  });

  it("falls back to the default orientation for an unknown orientation", () => {
    const r = resolvePropRegion(cat.props.bench, "zzz");
    expect(r).toEqual({ region: "side", mirror: false });
  });

  it("resolves the default state when no state is supplied", () => {
    const r = resolvePropRegion(cat.props.lantern, "s");
    expect(r.region).toBe("off");
  });

  it("resolves an explicit on/off state, overriding the orientation region", () => {
    expect(resolvePropRegion(cat.props.lantern, "s", "on").region).toBe("on0");
    expect(resolvePropRegion(cat.props.lantern, "s", "off").region).toBe("off");
  });
});
