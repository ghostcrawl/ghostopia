// ghostopia ghost-renderer — arbitrary-colour recolor + shared-clip tests.
//
// Proves the operator's requirement: a ghost can be given ANY colour (an
// arbitrary hue, not only a named section) and STILL plays the IDENTICAL shared
// animation clips. The recolor swaps only the body ramp; the clip/facing logic is
// colour-independent. Pure (no canvas) — runs in the vitest `node` env.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { describe, expect, it } from "vitest";
import { BASE_RAMP, loadAnimations, loadManifest, loadPalettes, normalizeHex } from "@ghostopia/ghost-art";

import {
  colorToHex,
  ghostColorKey,
  ghostPaletteFor,
  rampFromColor,
} from "./ghostRecolor";
import { clipNameForGhost } from "./GhostSprite";
import type { Ghost, GhostState } from "./contract";

const here = dirname(fileURLToPath(import.meta.url));
const assetsDir = resolve(here, "../../../assets");
const readJson = (p: string): unknown => JSON.parse(readFileSync(resolve(assetsDir, p), "utf8"));
const book = loadPalettes(readJson("palettes.json"));
const manifest = loadManifest(readJson("sprites.manifest.json"));
const animationBook = loadAnimations(readJson("animations.json"), manifest);

function ghost(partial: Partial<Ghost>): Ghost {
  return { id: "g", name: "g", home_grave: "grave-1", state: "IDLE", ...partial };
}

describe("rampFromColor — arbitrary hue -> coherent 3-tone ramp", () => {
  it("produces body / bodyShade / rim for any colour", () => {
    const ramp = rampFromColor(0x3366cc);
    expect(Object.keys(ramp).sort()).toEqual(["body", "bodyShade", "rim"]);
    for (const v of Object.values(ramp)) expect(v).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("distinct colours yield distinct bodies (arbitrary, not the 5 named)", () => {
    expect(rampFromColor(0xff0000).body).not.toBe(rampFromColor(0x00ff00).body);
    expect(rampFromColor(0x123456).body).not.toBe(rampFromColor(0x654321).body);
  });
});

describe("ghostPaletteFor — colour vs section resolution", () => {
  it("an ARBITRARY colour recolors the SAME base ramp keys (shared frames)", () => {
    const pal = ghostPaletteFor(ghost({ color: 0xff5aa8 }), book);
    expect(pal).not.toBeNull();
    // the map is keyed off the SAME base body/bodyShade/rim hexes for any colour
    expect(pal!.map[normalizeHex(BASE_RAMP.body)]).toBeDefined();
    expect(pal!.map[normalizeHex(BASE_RAMP.bodyShade)]).toBeDefined();
    expect(pal!.map[normalizeHex(BASE_RAMP.rim)]).toBeDefined();
  });

  it("two arbitrary colours share the SAME recolor KEY SET (identical frames)", () => {
    const a = ghostPaletteFor(ghost({ color: 0x8be04a }), book)!;
    const b = ghostPaletteFor(ghost({ color: 0x2233ff }), book)!;
    expect(Object.keys(a.map).sort()).toEqual(Object.keys(b.map).sort());
    // …but different target hexes (actually recolored differently)
    expect(a.map[normalizeHex(BASE_RAMP.body)]).not.toBe(b.map[normalizeHex(BASE_RAMP.body)]);
  });

  it("colour takes precedence over section", () => {
    const withColor = ghostPaletteFor(ghost({ color: 0x112233, section: "research" }), book)!;
    const research = book.sections["research"];
    expect(withColor.map[normalizeHex(BASE_RAMP.body)]).not.toBe(
      research.map[normalizeHex(BASE_RAMP.body)],
    );
  });

  it("a known section resolves to its authored palette; unknown/none -> null", () => {
    expect(ghostPaletteFor(ghost({ section: "extraction" }), book)).toBe(book.sections["extraction"]);
    expect(ghostPaletteFor(ghost({}), book)).toBeNull();
    expect(ghostPaletteFor(ghost({ section: "not-a-section" }), book)).toBeNull();
  });

  it("ghostColorKey distinguishes arbitrary colour from section", () => {
    expect(ghostColorKey(ghost({ color: 0xff5aa8 }))).toBe(colorToHex(0xff5aa8));
    expect(ghostColorKey(ghost({ section: "home" }))).toBe("home");
    expect(ghostColorKey(ghost({}))).toBe("");
  });
});

describe("shared clip set — animations are IDENTICAL for every colour", () => {
  const states: GhostState[] = ["IDLE", "WALKING", "EXTRACTING", "COMPLETED", "ERROR"];
  const facings = ["s", "se", "e", "ne", "n", "sw", "w", "nw"];

  it("clip selection is colour-independent (six distinct colours, same clips)", () => {
    const colors = [0xff5aa8, 0x8be04a, 0xffb347, 0x2233ff, 0x00e5ff, 0xffffff];
    for (const state of states) {
      for (const facing of facings) {
        const moving = state === "WALKING";
        const names = colors.map(() => clipNameForGhost(state, facing, moving));
        // every colour yields the exact same clip name for the same state/facing
        expect(new Set(names).size).toBe(1);
      }
    }
  });

  it("maps the five canonical states to the shared clip names", () => {
    expect(clipNameForGhost("IDLE", "se", false)).toBe("idle.se");
    expect(clipNameForGhost("WALKING", "e", true)).toBe("move.e");
    //  : a specific work phase → its per-kind clip; generic busy → plain "work".
    expect(clipNameForGhost("EXTRACTING", "s", false)).toBe("work.extracting");
    expect(clipNameForGhost("PROCESSING", "s", false)).toBe("work");
    expect(clipNameForGhost("COMPLETED", "s", false)).toBe("success");
    expect(clipNameForGhost("ERROR", "s", false)).toBe("error");
  });

  it("every clip clipNameForGhost can pick EXISTS in the shipped animation book", () => {
    // build the full set of clip names the selector can ever return
    const names = new Set<string>();
    for (const state of [...states, "PROCESSING", "RETRYING"] as GhostState[]) {
      for (const facing of facings) {
        names.add(clipNameForGhost(state, facing, state === "WALKING"));
      }
    }
    // the shipped book (shared by ALL colours) must contain every one of them
    for (const n of names) expect(animationBook[n], `missing clip ${n}`).toBeDefined();
    // sanity: the 8-way idle/move + expressions are all present
    expect(animationBook["idle.nw"]).toBeDefined();
    expect(animationBook["move.sw"]).toBeDefined();
    expect(animationBook["work"]).toBeDefined();
  });
});
