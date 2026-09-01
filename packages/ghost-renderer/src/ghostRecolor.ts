// ghostopia ghost-renderer — arbitrary-colour ghost recolor.
//
// One authored ghost family -> ANY colour, SHARED animations. A ghost may carry
// an arbitrary body colour (0xRRGGBB) or a named section. Either way the renderer
// swaps only the BODY RAMP (body/bodyShade/rim) of the ONE ghost sheet — the
// facing logic and the clip playback (idle/move/work/success/error) are
// identical for every colour. Named sections reuse their authored palette
// (research/extraction/verify/error/home); an arbitrary colour derives a
// coherent 3-tone ramp so the ghost stays readable and glowy at any hue.
//
// The PURE helpers (rampFromColor / ghostPaletteFor) are canvas-free and unit
// tested. The GhostFrameFactory uploads the recolored ghost frames as PixiJS
// textures, cached per colour key. Imports only PixiJS + @ghostopia/ghost-art —
// no SDK, no Python.

import { Texture } from "pixi.js";
import {
  BASE_RAMP,
  clampByte,
  makeRaster,
  recolor,
  sectionPaletteFromRamps,
  type Atlas,
  type PaletteBook,
  type Raster,
  type SectionPalette,
} from "@ghostopia/ghost-art";

import type { Ghost } from "./contract.js";

/** 0xRRGGBB -> "#rrggbb" (lowercase, no alpha). */
export function colorToHex(color: number): string {
  return `#${(color & 0xffffff).toString(16).padStart(6, "0")}`;
}

function channels(color: number): [number, number, number] {
  return [(color >> 16) & 0xff, (color >> 8) & 0xff, color & 0xff];
}

/** Linear per-channel mix of a colour toward a target (t in 0..1). */
function mix(color: number, toward: [number, number, number], t: number): string {
  const [r, g, b] = channels(color);
  const rr = clampByte(r + (toward[0] - r) * t);
  const gg = clampByte(g + (toward[1] - g) * t);
  const bb = clampByte(b + (toward[2] - b) * t);
  return `#${((rr << 16) | (gg << 8) | bb).toString(16).padStart(6, "0")}`;
}

const WHITE: [number, number, number] = [255, 255, 255];
const BLACK: [number, number, number] = [0, 0, 0];

/**
 * Derive a coherent 3-tone ghost ramp from a single arbitrary colour: a light
 * hued body, a mid shade, and a bright rim glow — matching the base ramp's
 * light/shade/rim structure so the SAME frames recolor cleanly at any hue.
 */
export function rampFromColor(color: number): Record<string, string> {
  return {
    body: mix(color, WHITE, 0.58), // light body carrying the hue
    bodyShade: mix(color, BLACK, 0.28), // mid shade under the body
    rim: mix(color, WHITE, 0.34), // bright hued rim glow
  };
}

/**
 * Resolve the SectionPalette (base-hex -> target-hex map) for a ghost:
 *   - an explicit `color` -> a derived ramp (arbitrary hue),
 *   - else a known `section` -> its authored palette,
 *   - else `null` (no recolor; render the base off-white family).
 * The map always keys off the SAME base body/bodyShade/rim hexes, so every
 * colour recolors the identical animation frames.
 */
export function ghostPaletteFor(
  ghost: Pick<Ghost, "color" | "section">,
  book: PaletteBook,
): SectionPalette | null {
  if (ghost.color !== undefined && ghost.color !== null) {
    return sectionPaletteFromRamps(colorToHex(ghost.color), BASE_RAMP, rampFromColor(ghost.color));
  }
  if (ghost.section && book.sections[ghost.section]) {
    return book.sections[ghost.section];
  }
  return null;
}

/** The colour-cache key for a ghost (its arbitrary colour, or its section, or ""). */
export function ghostColorKey(ghost: Pick<Ghost, "color" | "section">): string {
  if (ghost.color !== undefined && ghost.color !== null) return colorToHex(ghost.color);
  return ghost.section ?? "";
}

function cropRaster(atlas: Atlas, x: number, y: number, w: number, h: number): Raster {
  const out = makeRaster(w, h);
  for (let j = 0; j < h; j++) {
    for (let i = 0; i < w; i++) {
      const si = ((y + j) * atlas.width + (x + i)) * 4;
      const di = (j * w + i) * 4;
      out.data[di] = atlas.data[si];
      out.data[di + 1] = atlas.data[si + 1];
      out.data[di + 2] = atlas.data[si + 2];
      out.data[di + 3] = atlas.data[si + 3];
    }
  }
  return out;
}

function rasterToTexture(raster: Raster): Texture {
  const off = document.createElement("canvas");
  off.width = raster.width;
  off.height = raster.height;
  const ctx = off.getContext("2d");
  if (!ctx) throw new Error("ghostRecolor: 2D context unavailable");
  ctx.imageSmoothingEnabled = false;
  ctx.putImageData(new ImageData(new Uint8ClampedArray(raster.data), raster.width, raster.height), 0, 0);
  const tex = Texture.from(off);
  tex.source.scaleMode = "nearest";
  return tex;
}

/**
 * Builds + caches per-colour ghost frame sets from the packed atlas. Every colour
 * shares the SAME frame KEYS ("ghost:<facing>_<anim><n>", "ghost:work0", …) so
 * the clip playback + facing logic are identical; only the pixels are recolored.
 */
export class GhostFrameFactory {
  private readonly atlas: Atlas;
  private readonly book: PaletteBook;
  /** the base (uncolored) ghost frames — reused when a ghost has no palette. */
  private readonly baseGhostFrames: Record<string, Texture>;
  private readonly cache = new Map<string, Record<string, Texture>>();

  constructor(atlas: Atlas, baseFrames: Record<string, Texture>, book: PaletteBook) {
    this.atlas = atlas;
    this.book = book;
    this.baseGhostFrames = {};
    for (const [key, tex] of Object.entries(baseFrames)) {
      if (key.startsWith("ghost:")) this.baseGhostFrames[key] = tex;
    }
  }

  /** Resolve (and cache) the ghost frame set for a ghost's colour/section. */
  framesFor(ghost: Pick<Ghost, "color" | "section">): Record<string, Texture> {
    const key = ghostColorKey(ghost);
    const cached = this.cache.get(key);
    if (cached) return cached;

    const palette = ghostPaletteFor(ghost, this.book);
    if (!palette) {
      this.cache.set(key, this.baseGhostFrames);
      return this.baseGhostFrames;
    }

    const frames: Record<string, Texture> = {};
    for (const [frameKey, rect] of Object.entries(this.atlas.frames)) {
      if (!frameKey.startsWith("ghost:")) continue;
      const sub = cropRaster(this.atlas, rect.x, rect.y, rect.w, rect.h);
      frames[frameKey] = rasterToTexture(recolor(sub, palette));
    }
    this.cache.set(key, frames);
    return frames;
  }

  /** Free every recolored texture (the base frames are owned by the loop). */
  destroy(): void {
    for (const set of this.cache.values()) {
      if (set === this.baseGhostFrames) continue;
      for (const tex of Object.values(set)) tex.destroy(true);
    }
    this.cache.clear();
  }
}
