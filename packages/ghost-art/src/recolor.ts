// ghostopia ghost-art — runtime palette-swap recolor + per-status tint.
//
// `recolor` takes an already-resolved raster and swaps the base ramp for a
// section ramp (the SAME ghost grid becomes cool-blue for research, amber for
// extraction, ...). `applyStatusTint` multiplies a brightness/saturation (+
// optional colour blend) so error reads dark/desaturated and success bright.
// Both are pure: they return a NEW raster and never mutate their input.

import { cloneRaster, getPixel, setPixel, type Raster, type Rgba } from "./spriteGrid.js";
import {
  clampByte,
  DEFAULT_STATUS_TINTS,
  luma,
  normalizeHex,
  type SectionPalette,
  type StatusTint,
} from "./palette.js";
import { hexToRgba } from "./spriteGrid.js";

function pixelHex(r: number, g: number, b: number): string {
  const h = (n: number) => n.toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

/**
 * Swap the base ramp for a section ramp over a resolved raster. Opaque pixels
 * whose colour is in the section map are replaced (alpha preserved); everything
 * else — including transparent and unmapped pixels — is untouched.
 */
export function recolor(raster: Raster, section: SectionPalette): Raster {
  // pre-resolve the section map's target hexes to RGB once
  const resolved: Record<string, Rgba> = {};
  for (const [baseHex, sectionHex] of Object.entries(section.map)) {
    resolved[normalizeHex(baseHex)] = hexToRgba(sectionHex);
  }
  const out = cloneRaster(raster);
  for (let y = 0; y < raster.height; y++) {
    for (let x = 0; x < raster.width; x++) {
      const [r, g, b, a] = getPixel(raster, x, y);
      if (a === 0) continue; // transparent stays transparent
      const hit = resolved[pixelHex(r, g, b)];
      if (hit) setPixel(out, x, y, [hit[0], hit[1], hit[2], a]);
    }
  }
  return out;
}

/**
 * Apply a per-status tint (darken/desaturate for error, brighten for success).
 * `status` looks up `tints` (falling back to the built-in `DEFAULT_STATUS_TINTS`);
 * an unknown/neutral status is the identity transform.
 */
export function applyStatusTint(
  raster: Raster,
  status: string,
  tints: Record<string, StatusTint> = DEFAULT_STATUS_TINTS,
): Raster {
  const tint = tints[status];
  const out = cloneRaster(raster);
  if (!tint) return out; // neutral / unknown -> identity
  const { brightness, saturation } = tint;
  for (let y = 0; y < raster.height; y++) {
    for (let x = 0; x < raster.width; x++) {
      const [r, g, b, a] = getPixel(raster, x, y);
      if (a === 0) continue;
      // saturation: lerp toward luma
      const l = luma(r, g, b);
      let nr = l + (r - l) * saturation;
      let ng = l + (g - l) * saturation;
      let nb = l + (b - l) * saturation;
      // brightness multiply
      nr *= brightness;
      ng *= brightness;
      nb *= brightness;
      // optional colour blend
      if (tint.tint && tint.tintStrength) {
        const s = tint.tintStrength;
        nr = nr * (1 - s) + tint.tint[0] * s;
        ng = ng * (1 - s) + tint.tint[1] * s;
        nb = nb * (1 - s) + tint.tint[2] * s;
      }
      setPixel(out, x, y, [clampByte(nr), clampByte(ng), clampByte(nb), a]);
    }
  }
  return out;
}
