import { describe, it, expect } from "vitest";
import { loadAnimations } from "./animations";
import { loadManifest } from "./manifest";

// A tiny inline manifest so these tests are self-contained (they do NOT read the
// real assets/*.json — the real-file cross-check lives in manifest.test.ts).
const manifest = loadManifest({
  sprites: {
    ghost: {
      grid: "ghost",
      cellWidth: 12,
      cellHeight: 14,
      regions: {
        s_idle0: { col: 0, row: 0, cols: 1, rows: 1 },
        s_idle1: { col: 1, row: 0, cols: 1, rows: 1 },
        e_idle0: { col: 0, row: 2, cols: 1, rows: 1 },
      },
    },
    overlays: {
      grid: "overlays",
      cellWidth: 13,
      cellHeight: 13,
      regions: {
        captcha0: { col: 0, row: 0, cols: 1, rows: 1 },
        captcha1: { col: 1, row: 0, cols: 1, rows: 1 },
      },
    },
  },
});

const goodBook = {
  clips: {
    "idle.s": {
      frames: [
        { sprite: "ghost", region: "s_idle0", ms: 180 },
        { sprite: "ghost", region: "s_idle1", ms: 180 },
      ],
      loop: true,
    },
    "idle.w": {
      // west facing = mirror of the authored east art
      frames: [{ sprite: "ghost", region: "e_idle0", ms: 200 }],
      loop: true,
      mirror: true,
    },
    "ov.captcha": {
      frames: [
        { sprite: "overlays", region: "captcha0", ms: 300 },
        { sprite: "overlays", region: "captcha1", ms: 300 },
      ],
    },
  },
};

describe("loadAnimations — happy path", () => {
  it("loads a valid book into a typed AnimationBook", () => {
    const book = loadAnimations(goodBook, manifest);
    expect(Object.keys(book).sort()).toEqual(["idle.s", "idle.w", "ov.captcha"]);
    expect(book["idle.s"].frames).toHaveLength(2);
    expect(book["idle.s"].frames[0]).toEqual({
      sprite: "ghost",
      region: "s_idle0",
      ms: 180,
    });
  });

  it("defaults loop=true and mirror=false when omitted", () => {
    const book = loadAnimations(goodBook, manifest);
    expect(book["ov.captcha"].loop).toBe(true);
    expect(book["ov.captcha"].mirror).toBe(false);
    expect(book["idle.w"].mirror).toBe(true);
  });
});

describe("loadAnimations — validation (no invisible frames can ship)", () => {
  it("throws naming the missing sprite:region", () => {
    const bad = {
      clips: {
        "idle.s": { frames: [{ sprite: "ghost", region: "does_not_exist", ms: 100 }] },
      },
    };
    expect(() => loadAnimations(bad, manifest)).toThrow(/ghost:does_not_exist/);
  });

  it("throws when a frame references an unknown sprite", () => {
    const bad = {
      clips: { x: { frames: [{ sprite: "nope", region: "captcha0", ms: 100 }] } },
    };
    expect(() => loadAnimations(bad, manifest)).toThrow(/nope/);
  });

  it("throws on a non-positive ms", () => {
    const bad = {
      clips: { x: { frames: [{ sprite: "ghost", region: "s_idle0", ms: 0 }] } },
    };
    expect(() => loadAnimations(bad, manifest)).toThrow(/ms/);
  });

  it("throws on an empty frame list", () => {
    const bad = { clips: { x: { frames: [] } } };
    expect(() => loadAnimations(bad, manifest)).toThrow(/frame/i);
  });

  it("throws on a structurally invalid book", () => {
    expect(() => loadAnimations({ nope: true }, manifest)).toThrow();
  });
});
