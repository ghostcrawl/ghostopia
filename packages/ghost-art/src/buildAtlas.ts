// ghostopia ghost-art — pack manifest grids into a PixiJS-consumable atlas.
//
// `buildAtlas(manifest, grids)` resolves each catalogued grid (optionally with a
// 1px outline), blits it into a single RGBA atlas, and emits a frame table
// keyed `"<sprite>:<region>"` -> { x, y, w, h } computed from each region's cell
// coordinates. It has NO renderer dependency — it returns raw pixel data + a
// frame map; the renderer uploads this as a texture with
// `imageSmoothingEnabled = false` / nearest scaling for crisp pixels.

import { outline as outlinePass } from "./outline.js";
import { makeRaster, resolveGrid, type Raster, type Rgba, type SpriteGrid } from "./spriteGrid.js";
import type { SpriteManifest } from "./manifest.js";

/** A packed frame rectangle in atlas pixel coordinates. */
export interface AtlasFrame {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A packed atlas: raw RGBA data + a per-sprite:region frame table. */
export interface Atlas {
  width: number;
  height: number;
  data: Uint8ClampedArray;
  frames: Record<string, AtlasFrame>;
  /** documented render intent — the renderer must NOT smooth these pixels. */
  imageSmoothing: false;
}

export interface BuildAtlasOptions {
  /** apply the 1px outline pass to every sprite (default true). */
  outline?: boolean;
  /** outline colour when `outline` is on. */
  outlineColor?: Rgba;
  /** 1px transparent gutter between packed sprites (default 1) to avoid bleed. */
  padding?: number;
}

function blit(dst: Raster, src: Raster, ox: number, oy: number): void {
  for (let y = 0; y < src.height; y++) {
    for (let x = 0; x < src.width; x++) {
      const si = (y * src.width + x) * 4;
      const dx = ox + x;
      const dy = oy + y;
      if (dx < 0 || dy < 0 || dx >= dst.width || dy >= dst.height) continue;
      const di = (dy * dst.width + dx) * 4;
      dst.data[di] = src.data[si];
      dst.data[di + 1] = src.data[si + 1];
      dst.data[di + 2] = src.data[si + 2];
      dst.data[di + 3] = src.data[si + 3];
    }
  }
}

/**
 * Pack the manifest's grids into one atlas. Sprites are stacked vertically
 * (simple, deterministic shelf packing); the atlas width is the widest sprite.
 */
export function buildAtlas(
  manifest: SpriteManifest,
  grids: Record<string, SpriteGrid>,
  options: BuildAtlasOptions = {},
): Atlas {
  const doOutline = options.outline ?? true;
  const padding = options.padding ?? 1;

  // resolve every unique referenced grid once
  const resolved = new Map<string, Raster>();
  for (const entry of Object.values(manifest.sprites)) {
    if (resolved.has(entry.grid)) continue;
    const grid = grids[entry.grid];
    if (!grid) throw new Error(`buildAtlas: missing grid "${entry.grid}"`);
    let raster = resolveGrid(grid);
    if (doOutline) raster = outlinePass(raster, options.outlineColor);
    resolved.set(entry.grid, raster);
  }

  // measure — one vertical shelf per sprite entry
  const spriteNames = Object.keys(manifest.sprites);
  let atlasWidth = 1;
  let atlasHeight = 0;
  const placement: Record<string, { raster: Raster; y: number }> = {};
  for (const name of spriteNames) {
    const entry = manifest.sprites[name];
    const raster = resolved.get(entry.grid)!;
    atlasWidth = Math.max(atlasWidth, raster.width);
    placement[name] = { raster, y: atlasHeight };
    atlasHeight += raster.height + padding;
  }
  if (atlasHeight === 0) atlasHeight = 1;

  const atlas = makeRaster(atlasWidth, atlasHeight);
  const frames: Record<string, AtlasFrame> = {};

  for (const name of spriteNames) {
    const entry = manifest.sprites[name];
    const { raster, y: oy } = placement[name];
    blit(atlas, raster, 0, oy);
    for (const [rn, region] of Object.entries(entry.regions)) {
      frames[`${name}:${rn}`] = {
        x: region.col * entry.cellWidth,
        y: oy + region.row * entry.cellHeight,
        w: region.cols * entry.cellWidth,
        h: region.rows * entry.cellHeight,
      };
    }
  }

  return {
    width: atlas.width,
    height: atlas.height,
    data: atlas.data,
    frames,
    imageSmoothing: false,
  };
}
